"""One-time AWS setup so contractor replies reach Outreach without inbox polling.

    python -m backend.ses_setup --domain reply.yourdomain.com [--region us-east-1]

Creates (re-running is safe):
  - SQS queue  dispatch-replies          the backend long-polls this
  - SNS topic  dispatch-replies          SES publishes each email here → SQS
  - SES domain identity + receipt rule   accepts mail for quotes@<domain>

Then prints the DNS records to add at your registrar and the .env lines.
SES only receives mail in us-east-1, us-west-2 and eu-west-1.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from dotenv import load_dotenv

load_dotenv()

NAME = "dispatch-replies"
RULE_SET = "dispatch"


def _client(service: str, region: str):
    import boto3

    return boto3.client(
        service,
        aws_access_key_id=os.getenv("AWS_ACCESS_KEY_ID"),
        aws_secret_access_key=os.getenv("AWS_SECRET_ACCESS_KEY"),
        aws_session_token=os.getenv("AWS_SESSION_TOKEN"),
        region_name=region,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--domain", required=True, help="domain that will receive replies")
    parser.add_argument("--local-part", default="quotes", help="mailbox name (default: quotes)")
    parser.add_argument("--region", default="us-east-1")
    args = parser.parse_args()
    domain = args.domain.lower().strip(".")
    address = f"{args.local_part}@{domain}"

    sqs = _client("sqs", args.region)
    sns = _client("sns", args.region)
    ses = _client("ses", args.region)
    account = _client("sts", args.region).get_caller_identity()["Account"]

    queue_url = sqs.create_queue(QueueName=NAME)["QueueUrl"]
    queue_arn = sqs.get_queue_attributes(QueueUrl=queue_url, AttributeNames=["QueueArn"])[
        "Attributes"
    ]["QueueArn"]
    topic_arn = sns.create_topic(Name=NAME)["TopicArn"]
    print(f"✓ SQS queue  {queue_url}")
    print(f"✓ SNS topic  {topic_arn}")

    # SNS may write to the queue; SES may publish to the topic.
    sqs.set_queue_attributes(
        QueueUrl=queue_url,
        Attributes={
            "Policy": json.dumps(
                {
                    "Version": "2012-10-17",
                    "Statement": [
                        {
                            "Effect": "Allow",
                            "Principal": {"Service": "sns.amazonaws.com"},
                            "Action": "sqs:SendMessage",
                            "Resource": queue_arn,
                            "Condition": {"ArnEquals": {"aws:SourceArn": topic_arn}},
                        }
                    ],
                }
            )
        },
    )
    sns.set_topic_attributes(
        TopicArn=topic_arn,
        AttributeName="Policy",
        AttributeValue=json.dumps(
            {
                "Version": "2012-10-17",
                "Statement": [
                    {
                        "Effect": "Allow",
                        "Principal": {"Service": "ses.amazonaws.com"},
                        "Action": "sns:Publish",
                        "Resource": topic_arn,
                        "Condition": {"StringEquals": {"AWS:SourceAccount": account}},
                    }
                ],
            }
        ),
    )
    sns.subscribe(TopicArn=topic_arn, Protocol="sqs", Endpoint=queue_arn)
    print("✓ SNS → SQS subscription")

    token = ses.verify_domain_identity(Domain=domain)["VerificationToken"]
    print(f"✓ SES identity {domain} (pending DNS verification)")

    try:
        ses.create_receipt_rule_set(RuleSetName=RULE_SET)
    except ses.exceptions.AlreadyExistsException:
        pass
    rule = {
        "Name": NAME,
        "Enabled": True,
        "Recipients": [address],
        "ScanEnabled": True,
        "Actions": [{"SNSAction": {"TopicArn": topic_arn, "Encoding": "UTF-8"}}],
    }
    try:
        ses.create_receipt_rule(RuleSetName=RULE_SET, Rule=rule)
    except ses.exceptions.AlreadyExistsException:
        ses.update_receipt_rule(RuleSetName=RULE_SET, Rule=rule)
    ses.set_active_receipt_rule_set(RuleSetName=RULE_SET)
    print(f"✓ SES receipt rule for {address} (active)")

    print(
        f"""
Add these DNS records for {domain}:

  TXT  _amazonses.{domain}   "{token}"
  MX   {domain}              10 inbound-smtp.{args.region}.amazonaws.com

Add to .env:

  REPLY_QUEUE_URL={queue_url}
  REPLY_TO_ADDRESS={address}
  # optional: teammates who play the closest fixers in the demo
  OUTREACH_DEMO_INBOXES=teammate1@gmail.com,teammate2@gmail.com

Then in Gmail (the account Outreach sends from): Settings → Accounts →
"Send mail as" → edit info → "Specify a different reply-to address" → {address}
"""
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
