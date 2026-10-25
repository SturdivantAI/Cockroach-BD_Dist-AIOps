"""Publish the verified image without leaving an ECR login token on disk."""

import base64
import json
import subprocess
import tempfile
from pathlib import Path

import boto3


def main() -> None:
    root = Path(__file__).resolve().parents[2]
    target = Path(__file__).with_name("deployment-state.json")
    state = json.loads(target.read_text())
    release = json.loads((root / "release/verification-20261024.json").read_text())
    local = subprocess.run(
        ["docker", "image", "inspect", release["image_tag"], "--format", "{{.Id}}"],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    if local not in {release["image_config_digest"], release["image_manifest_digest"]}:
        raise RuntimeError("Local image differs from the verified release")
    ecr = boto3.client("ecr", region_name=state["region"])
    suffix = release["image_manifest_digest"].split(":")[1][:8]
    tag = "v0.2.0-" + suffix
    uri = state["repository_uri"] + ":" + tag
    exists = ecr.batch_get_image(repositoryName="sre-agent-v2", imageIds=[{"imageTag": tag}])["images"]
    if not exists:
        auth = ecr.get_authorization_token()["authorizationData"][0]
        username, password = base64.b64decode(auth["authorizationToken"]).decode().split(":", 1)
        with tempfile.TemporaryDirectory(prefix="sre-ecr-login-") as directory:
            docker = ["docker", "--config", directory]
            login = subprocess.run(
                docker + ["login", "--username", username, "--password-stdin", auth["proxyEndpoint"]],
                input=password, text=True, capture_output=True,
            )
            if login.returncode:
                raise RuntimeError("ECR login failed")
            subprocess.run(docker + ["tag", release["image_tag"], uri], check=True)
            subprocess.run(docker + ["push", uri], check=True)
    image = ecr.describe_images(repositoryName="sre-agent-v2", imageIds=[{"imageTag": tag}])["imageDetails"][0]
    if image["imageDigest"] != release["image_manifest_digest"]:
        raise RuntimeError("Published image digest differs from verified manifest")
    state["image_uri"] = state["repository_uri"] + "@" + image["imageDigest"]
    state["image_tag"] = tag
    target.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    print("Published verified image:", state["image_uri"])


if __name__ == "__main__":
    main()
