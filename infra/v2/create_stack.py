"""Create the demo hosting stack from the prepared deployment state."""

import json
from pathlib import Path

import boto3


def main() -> None:
    directory = Path(__file__).resolve().parent
    state = json.loads((directory / "deployment-state.json").read_text())
    values = {
        "VpcId": state["vpc_id"],
        "PublicSubnets": "subnet-048d04bc771caadeb,subnet-08770d88d9a852c7a",
        "VpcCidr": "172.31.0.0/16",
        "DatabaseSecurityGroupId": state["database_security_group"],
        "DatabaseHost": state["database_host"],
        "ApplicationDatabaseSecretArn": state["app_db_secret_arn"],
        "RuntimeSecretArn": state["runtime_secret_arn"],
        "ImageUri": state["image_uri"],
        "CloudFrontPrefixListId": "pl-3b927c52",
    }
    client = boto3.client("cloudformation", region_name=state["region"])
    result = client.create_stack(
        StackName="sre-agent-v2-demo",
        TemplateBody=(directory / "service.json").read_text(),
        Parameters=[{"ParameterKey": key, "ParameterValue": value} for key, value in values.items()],
        Capabilities=["CAPABILITY_IAM"],
        Tags=[{"Key": "Project", "Value": "SRE-Agent-v2"}],
    )
    state["stack_id"] = result["StackId"]
    (directory / "deployment-state.json").write_text(json.dumps(state, indent=2) + "\n")
    print("Hosting stack creation started:", result["StackId"])


if __name__ == "__main__":
    main()
