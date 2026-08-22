from typing import Any

from aws_cdk import (
    Acknowledgment,
    CfnOutput,
    RemovalPolicy,
    Stack,
    Tags,
    Validations,
)
from aws_cdk import (
    aws_dynamodb as dynamodb,
)
from aws_cdk import (
    aws_iam as iam,
)
from constructs import Construct

NO_PITR = (
    "Point-in-time recovery is off. The table holds the request ledger and the quota bucket, "
    "both caches: raw/ is the record of what was fetched, and losing the ledger costs one "
    "unconditional refetch per url, not data. Restoring it would be slower than refilling it. "
    "Accepted risk."
)


class IngestStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("component", "ingest")

        self.table_name = f"pitadvisor-ingest-{env_name}"
        self.table = dynamodb.TableV2(
            self,
            "IngestTable",
            table_name=self.table_name,
            partition_key=dynamodb.Attribute(name="pk", type=dynamodb.AttributeType.STRING),
            billing=dynamodb.Billing.on_demand(),
            time_to_live_attribute="expires_at",
            encryption=dynamodb.TableEncryptionV2.aws_managed_key(),
            removal_policy=RemovalPolicy.DESTROY,
        )
        Validations.of(self.table).acknowledge(
            Acknowledgment(id="AwsSolutions-DDB3", reason=NO_PITR)
        )

        dev_user = iam.User.from_user_name(
            self,
            "DevUser",
            self.node.try_get_context("devUserName") or "pitadvisor-dev",
        )
        iam.Policy(
            self,
            "DevAccess",
            policy_name=f"pitadvisor-ingest-access-{env_name}",
            users=[dev_user],
            statements=[
                iam.PolicyStatement(
                    actions=[
                        "dynamodb:GetItem",
                        "dynamodb:PutItem",
                        "dynamodb:UpdateItem",
                        "dynamodb:DeleteItem",
                        "dynamodb:Query",
                        "dynamodb:DescribeTable",
                    ],
                    resources=[self.table.table_arn],
                ),
            ],
        )
        CfnOutput(self, "IngestTableName", value=self.table_name)
