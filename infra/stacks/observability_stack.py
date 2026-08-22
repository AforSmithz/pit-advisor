from typing import Any

from aws_cdk import Acknowledgment, CfnOutput, Stack, Tags, Validations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_iam as iam
from constructs import Construct

PROJECT_LIMIT_USD = 20
ACCOUNT_LIMIT_USD = 40

COST_EXPLORER_WILDCARD = (
    "Cost Explorer has no resource-level permissions, so every ce: action is Resource '*' or "
    "nothing at all. Limited to three actions on one IAM user."
)


def _notifications(
    email: str | None,
) -> list[budgets.CfnBudget.NotificationWithSubscribersProperty] | None:
    if not email:
        return None
    subscribers = [budgets.CfnBudget.SubscriberProperty(address=email, subscription_type="EMAIL")]
    return [
        budgets.CfnBudget.NotificationWithSubscribersProperty(
            notification=budgets.CfnBudget.NotificationProperty(
                comparison_operator="GREATER_THAN",
                notification_type=kind,
                threshold=threshold,
                threshold_type="PERCENTAGE",
            ),
            subscribers=subscribers,
        )
        for kind, threshold in (("ACTUAL", 80), ("FORECASTED", 100))
    ]


class ObservabilityStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        alert_email: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("component", "observability")

        notifications = _notifications(alert_email)
        project_budget_name = self.node.try_get_context("budgetName") or "pit-advisor-monthly"

        budgets.CfnBudget(
            self,
            "ProjectBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name=project_budget_name,
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=PROJECT_LIMIT_USD, unit="USD"),
                # this reports nothing until "project" is activated as a cost allocation tag in
                # billing, which is a console/API action cloudformation cannot do
                cost_filters={"TagKeyValue": ["user:project$pit-advisor"]},
            ),
            notifications_with_subscribers=notifications,
        )

        # the account is shared with an unrelated workload drawing on the same credits, so the
        # tag-filtered budget above cannot see what is actually burning them
        budgets.CfnBudget(
            self,
            "AccountBudget",
            budget=budgets.CfnBudget.BudgetDataProperty(
                budget_name="pit-advisor-account-monthly",
                budget_type="COST",
                time_unit="MONTHLY",
                budget_limit=budgets.CfnBudget.SpendProperty(amount=ACCOUNT_LIMIT_USD, unit="USD"),
            ),
            notifications_with_subscribers=notifications,
        )

        dev_user = iam.User.from_user_name(
            self,
            "DevUser",
            self.node.try_get_context("devUserName") or "pitadvisor-dev",
        )
        dev_access = iam.ManagedPolicy(
            self,
            "DevManagedAccess",
            managed_policy_name=f"pitadvisor-cost-access-{env_name}",
            users=[dev_user],
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "ce:GetCostAndUsage",
                        "ce:ListCostAllocationTags",
                        "ce:UpdateCostAllocationTagsStatus",
                    ],
                    resources=["*"],
                )
            ],
        )
        Validations.of(dev_access).acknowledge(
            Acknowledgment(id="AwsSolutions-IAM5[Resource::*]", reason=COST_EXPLORER_WILDCARD)
        )

        CfnOutput(self, "ProjectBudgetName", value=project_budget_name)
        CfnOutput(self, "AccountBudgetName", value="pit-advisor-account-monthly")
        CfnOutput(self, "BudgetAlertsConfigured", value=str(notifications is not None).lower())
