from typing import Any

from aws_cdk import Acknowledgment, CfnOutput, Duration, Stack, Tags, Validations
from aws_cdk import aws_budgets as budgets
from aws_cdk import aws_cloudwatch as cloudwatch
from aws_cdk import aws_iam as iam
from constructs import Construct

PROJECT_LIMIT_USD = 20
ACCOUNT_LIMIT_USD = 40

ANSWER_PROFILE = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
EMBEDDING_MODEL = "cohere.embed-english-v3"
PERIOD = Duration.hours(1)

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

        dashboard = self._dashboard(env_name)

        CfnOutput(self, "DashboardName", value=dashboard.dashboard_name)
        CfnOutput(self, "ProjectBudgetName", value=project_budget_name)
        CfnOutput(self, "AccountBudgetName", value="pit-advisor-account-monthly")
        CfnOutput(self, "BudgetAlertsConfigured", value=str(notifications is not None).lower())

    def _dashboard(self, env_name: str) -> cloudwatch.Dashboard:
        """Latency and failure for every moving part. Resources are found by the names the other
        stacks give them, not by reference, so this stack still deploys first and alone."""
        workgroup = self.node.try_get_context("athenaWorkgroup") or "pitadvisor"
        machines = {
            "weekend": f"pitadvisor-weekend-{env_name}",
            "backfill": f"pitadvisor-backfill-{env_name}",
        }
        functions = {
            "ask": f"pitadvisor-ask-{env_name}",
            "race sim": f"pitadvisor-race-sim-{env_name}",
        }

        def machine(metric: str, name: str, statistic: str, label: str) -> cloudwatch.Metric:
            arn = f"arn:aws:states:{self.region}:{self.account}:stateMachine:{name}"
            return cloudwatch.Metric(
                namespace="AWS/States",
                metric_name=metric,
                dimensions_map={"StateMachineArn": arn},
                statistic=statistic,
                period=PERIOD,
                label=label,
            )

        def function(metric: str, name: str, statistic: str, label: str) -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/Lambda",
                metric_name=metric,
                dimensions_map={"FunctionName": name},
                statistic=statistic,
                period=PERIOD,
                label=label,
            )

        def model(metric: str, model_id: str, statistic: str, label: str) -> cloudwatch.Metric:
            return cloudwatch.Metric(
                namespace="AWS/Bedrock",
                metric_name=metric,
                dimensions_map={"ModelId": model_id},
                statistic=statistic,
                period=PERIOD,
                label=label,
            )

        # the knowledge base calls cohere by its arn and the laptop by its bare id, and bedrock
        # keeps them as two separate series
        embedding_ids = {
            "embed (kb)": f"arn:aws:bedrock:{self.region}::foundation-model/{EMBEDDING_MODEL}",
            "embed (direct)": EMBEDDING_MODEL,
        }

        dashboard = cloudwatch.Dashboard(
            self,
            "Dashboard",
            dashboard_name=f"pitadvisor-{env_name}",
            default_interval=Duration.days(7),
        )
        dashboard.add_widgets(
            cloudwatch.TextWidget(
                markdown=(
                    "# Pit Advisor\nHourly buckets. Spend is not here: CloudWatch billing metrics "
                    "need a root setting and lag a day, so `pitadv cost-report` reads Cost "
                    "Explorer instead and the dashboard's pipeline page shows it."
                ),
                width=24,
                height=2,
            )
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Pipeline run time (max, ms)",
                left=[
                    machine("ExecutionTime", name, "Maximum", key) for key, name in machines.items()
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Pipeline runs",
                left=[
                    machine(metric, name, "Sum", f"{key} {word}")
                    for key, name in machines.items()
                    for metric, word in (
                        ("ExecutionsSucceeded", "ok"),
                        ("ExecutionsFailed", "failed"),
                        ("ExecutionsTimedOut", "timed out"),
                    )
                ],
                width=12,
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Function duration (ms)",
                left=[
                    function("Duration", name, statistic, f"{key} {statistic.lower()}")
                    for key, name in functions.items()
                    for statistic in ("p50", "p95", "Maximum")
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Function calls and failures",
                left=[
                    function(metric, name, "Sum", f"{key} {metric.lower()}")
                    for key, name in functions.items()
                    for metric in ("Invocations", "Errors", "Throttles")
                ],
                width=12,
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Model latency (ms)",
                left=[
                    model(
                        "InvocationLatency", ANSWER_PROFILE, statistic, f"haiku {statistic.lower()}"
                    )
                    for statistic in ("p50", "p95")
                ]
                + [
                    model("InvocationLatency", model_id, "p95", f"{key} p95")
                    for key, model_id in embedding_ids.items()
                ],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="Model refusals",
                left=[
                    model("InvocationThrottles", ANSWER_PROFILE, "Sum", "haiku throttled"),
                    model("InvocationClientErrors", ANSWER_PROFILE, "Sum", "haiku client errors"),
                    model("InvocationServerErrors", ANSWER_PROFILE, "Sum", "haiku server errors"),
                ],
                width=8,
            ),
            cloudwatch.GraphWidget(
                title="Model tokens",
                left=[
                    model("InputTokenCount", ANSWER_PROFILE, "Sum", "haiku in"),
                    model("OutputTokenCount", ANSWER_PROFILE, "Sum", "haiku out"),
                ]
                + [
                    model("InputTokenCount", model_id, "Sum", key)
                    for key, model_id in embedding_ids.items()
                ],
                width=8,
            ),
        )
        dashboard.add_widgets(
            cloudwatch.GraphWidget(
                title="Athena bytes scanned",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/Athena",
                        metric_name="ProcessedBytes",
                        dimensions_map={
                            "WorkGroup": workgroup,
                            "QueryState": "SUCCEEDED",
                            "QueryType": "DML",
                        },
                        statistic="Sum",
                        period=PERIOD,
                        label="scanned",
                    )
                ],
                width=12,
            ),
            cloudwatch.GraphWidget(
                title="Athena query time (ms)",
                left=[
                    cloudwatch.Metric(
                        namespace="AWS/Athena",
                        metric_name="TotalExecutionTime",
                        dimensions_map={
                            "WorkGroup": workgroup,
                            "QueryState": "SUCCEEDED",
                            "QueryType": "DML",
                        },
                        statistic=statistic,
                        period=PERIOD,
                        label=statistic.lower(),
                    )
                    for statistic in ("p50", "Maximum")
                ],
                width=12,
            ),
        )
        return dashboard
