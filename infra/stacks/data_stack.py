from typing import Any

from aws_cdk import (
    Acknowledgment,
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    Validations,
)
from aws_cdk import (
    aws_athena as athena,
)
from aws_cdk import (
    aws_glue as glue,
)
from aws_cdk import (
    aws_iam as iam,
)
from aws_cdk import (
    aws_s3 as s3,
)
from constructs import Construct

SCAN_CAP_BYTES = 1024**3

OBJECT_WILDCARD = (
    "Read access covers the whole bucket by design. This is a lake: keys are generated per "
    "source, season, event and session, so a prefix allowlist would need rewriting on every "
    "schema change. The grant is still scoped to these two buckets and to read actions."
)

NO_ACCESS_LOGS = (
    "Server access logging is deliberately off. This is a single-user analytics account with no "
    "third-party writers; object-level access logs would be a second unbounded copy of the data "
    "bucket's traffic inside a $20/month cap, and the CloudTrail management trail already covers "
    "the control plane. Accepted risk."
)


class DataStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        env_name: str,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("component", "data")

        bucket_name = (
            self.node.try_get_context("dataBucket") or f"pit-advisor-data-{env_name}-{self.account}"
        )

        self.bucket = s3.Bucket(
            self,
            "DataBucket",
            bucket_name=bucket_name,
            versioned=True,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-noncurrent",
                    # raw/ is immutable, so a noncurrent version means something overwrote it.
                    # 30 days is long enough to notice and replay, short enough not to accumulate.
                    noncurrent_version_expiration=Duration.days(30),
                    abort_incomplete_multipart_upload_after=Duration.days(7),
                )
            ],
        )

        self.results_bucket = s3.Bucket(
            self,
            "AthenaResultsBucket",
            bucket_name=f"pit-advisor-athena-results-{env_name}-{self.account}",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            lifecycle_rules=[
                s3.LifecycleRule(
                    id="expire-results",
                    expiration=Duration.days(7),
                    abort_incomplete_multipart_upload_after=Duration.days(1),
                )
            ],
        )

        for bucket in (self.bucket, self.results_bucket):
            Validations.of(bucket).acknowledge(
                Acknowledgment(id="AwsSolutions-S1", reason=NO_ACCESS_LOGS)
            )

        self.database_name = f"pitadvisor_{env_name}"
        self.database = glue.CfnDatabase(
            self,
            "Database",
            catalog_id=self.account,
            database_input=glue.CfnDatabase.DatabaseInputProperty(
                name=self.database_name,
                description="pit advisor medallion catalog",
                location_uri=f"s3://{self.bucket.bucket_name}/",
            ),
        )

        self.workgroup_name = self.node.try_get_context("athenaWorkgroup") or "pitadvisor"
        self.workgroup = athena.CfnWorkGroup(
            self,
            "WorkGroup",
            name=self.workgroup_name,
            description="byte-capped workgroup for pit advisor marts",
            state="ENABLED",
            work_group_configuration=athena.CfnWorkGroup.WorkGroupConfigurationProperty(
                enforce_work_group_configuration=True,
                publish_cloud_watch_metrics_enabled=True,
                requester_pays_enabled=False,
                bytes_scanned_cutoff_per_query=SCAN_CAP_BYTES,
                engine_version=athena.CfnWorkGroup.EngineVersionProperty(
                    selected_engine_version="AUTO"
                ),
                result_configuration=athena.CfnWorkGroup.ResultConfigurationProperty(
                    output_location=f"s3://{self.results_bucket.bucket_name}/athena/",
                    encryption_configuration=athena.CfnWorkGroup.EncryptionConfigurationProperty(
                        encryption_option="SSE_S3"
                    ),
                ),
            ),
        )

        dev_user = iam.User.from_user_name(
            self,
            "DevUser",
            self.node.try_get_context("devUserName") or "pitadvisor-dev",
        )
        # the policy is named per stack: two stacks attaching an unnamed policy to the same user
        # generate the same physical name and the second deploy fails
        dev_access = iam.Policy(
            self,
            "DevAccess",
            policy_name=f"pitadvisor-lake-access-{env_name}",
            users=[dev_user],
            statements=[
                iam.PolicyStatement(
                    actions=["s3:ListBucket", "s3:GetBucketLocation"],
                    resources=[self.bucket.bucket_arn, self.results_bucket.bucket_arn],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject"],
                    resources=[self.bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["s3:GetObject", "s3:PutObject", "s3:DeleteObject"],
                    resources=[self.results_bucket.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=["athena:GetWorkGroup", "athena:StartQueryExecution"],
                    resources=[
                        self.format_arn(
                            service="athena",
                            resource="workgroup",
                            resource_name=self.workgroup_name,
                        )
                    ],
                ),
            ],
        )
        Validations.of(dev_access).acknowledge(
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<DataBucketE3889A50.Arn>/*]",
                reason=OBJECT_WILDCARD,
            ),
            Acknowledgment(
                id="AwsSolutions-IAM5[Resource::<AthenaResultsBucket879938FA.Arn>/*]",
                reason=OBJECT_WILDCARD,
            ),
        )

        CfnOutput(self, "DataBucketName", value=self.bucket.bucket_name)
        CfnOutput(self, "AthenaResultsBucketName", value=self.results_bucket.bucket_name)
        CfnOutput(self, "GlueDatabaseName", value=self.database_name)
        CfnOutput(self, "AthenaWorkGroupName", value=self.workgroup_name)
