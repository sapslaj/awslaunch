import argparse
import os
import re
import sys
import urllib.parse
import webbrowser

import boto3
import ruyaml
from pyfzf.pyfzf import FzfPrompt

CMD_END = ";"

ACTIONS = {
    "assume": "Assume the role in the current shell",
    "browser": "Open browser to the switch role URL",
    "url": "Print the Switch Role URL",
    "role": "Print the role ARN",
}


def generate_account_choices(account_display_names, organizations_client):
    accounts = [
        account | {"DisplayName": account_display_names.get(int(account["Id"]), account["Name"])}
        for account in organizations_client.list_accounts()["Accounts"]
    ]
    return dict([(f'{account["DisplayName"]} ({account["Id"]})', account) for account in accounts])


def generate_role_arn(account_id, role_name):
    return f"arn:aws:iam::{account_id}:role/{role_name}"


def generate_url(role_name, account_id, display_name):
    encoded_display_name = urllib.parse.quote_plus(display_name)
    return f"https://signin.aws.amazon.com/switchrole?roleName={role_name}&account={account_id}&displayName={encoded_display_name}"


def generate_session_name(sts_client):
    caller_arn = sts_client.get_caller_identity()["Arn"]
    return re.findall(r".+/(.+)", caller_arn)[0]


def generate_session_credentials_commands(sts_client, role_arn, session_name, duration_hours=1, external_id=""):
    duration_seconds = 3600 * duration_hours
    kwargs = {
        "RoleArn": role_arn,
        "RoleSessionName": session_name,
        "DurationSeconds": duration_seconds,
    }
    if external_id:
        kwargs["ExternalId"] = external_id
    credentials = sts_client.assume_role(**kwargs)["Credentials"]
    return CMD_END.join(
        [
            f'export AWS_ACCESS_KEY_ID="{credentials["AccessKeyId"]}"',
            f'export AWS_SECRET_ACCESS_KEY="{credentials["SecretAccessKey"]}"',
            f'export AWS_SESSION_TOKEN="{credentials["SessionToken"]}"',
        ]
    )


def choose_account(account_choices, account_id=None):
    if account_id:
        return [account for account in account_choices.values() if account["Id"] == account_id][0]
    fzf = FzfPrompt()
    choice = fzf.prompt(sorted(account_choices.keys()))
    return account_choices[choice[0]]


def choose_role_name(role_map, account_id):
    default_roles = role_map.get("_", ["OrganizationAccountAccessRole"])
    roles = role_map.get(int(account_id), default_roles)
    if len(roles) == 1:
        return roles[0]
    fzf = FzfPrompt()
    choice = fzf.prompt(sorted(roles))
    return choice[0]


def choose_actions(args):
    passed_actions = [action for action in ACTIONS.keys() if getattr(args, action)]
    if passed_actions:
        return passed_actions
    fzf = FzfPrompt()
    choices = dict([(f"{action}\t{help}", action) for action, help in ACTIONS.items()])
    chosen = fzf.prompt(choices, "--multi")
    return [choices[choice] for choice in chosen]


def main(config, args):
    source_profile = args.source_profile or config.get("source_profile", "default")
    source_session = boto3.Session(profile_name=source_profile)
    organizations_profile = args.organizations_profile or config.get("organizations_profile", source_profile)
    organizations_session = boto3.Session(profile_name=organizations_profile)
    organizations_client = organizations_session.client("organizations")
    sts_client = source_session.client("sts")

    account_choices = generate_account_choices(
        account_display_names=config.get("account_display_names", {}), organizations_client=organizations_client
    )
    account = choose_account(account_choices=account_choices, account_id=args.account_id)
    account_id = account["Id"]
    account_display_name = account["DisplayName"]
    role_name = args.role_name or choose_role_name(
        account_id=account_id,
        role_map=config.get("roles", {}),
    )
    role_arn = generate_role_arn(account_id=account_id, role_name=role_name)
    url = generate_url(role_name=role_name, account_id=account_id, display_name=account_display_name)
    actions = choose_actions(args)

    if not actions:
        raise Exception(f"Oh dang this should never happen, action is {actions}")
    if "assume" in actions:
        session_name = config.get("role_session_name") or generate_session_name(sts_client=sts_client) or "awslaunch"
        duration_hours = int(args.duration_hours or config.get("duration_hours", 1))
        session_credentials_commands = generate_session_credentials_commands(
            sts_client=sts_client,
            role_arn=role_arn,
            session_name=session_name,
            duration_hours=duration_hours,
        )
        print(session_credentials_commands, end=CMD_END)
        print(f"echo '{role_arn}' from '{account_display_name}' assumed.", end=CMD_END)
    if "browser" in actions:
        print(f"echo opening browser to '{url}'", end=CMD_END)
        webbrowser.open(url)
    if "url" in actions:
        print(f"echo '{url}'", end=CMD_END)
    if "role" in actions:
        print(f"echo '{role_arn}'", end=CMD_END)
    print()
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(prog="awslaunch", add_help=False)
    parser.add_argument("--help", "-h", action="store_true", help="show this help message and exit")
    for action, help in ACTIONS.items():
        parser.add_argument(f"--{action}", f"-{action[0]}", action="store_true", help=help)
    parser.add_argument(
        "--external-id",
        required=False,
        default="",
        help="Use external ID when assuming role (doesn't work in the browser)",
    )
    parser.add_argument("--role-name", required=False, default=None, help="Pass the role name explicitly")
    parser.add_argument("--account-id", required=False, default=None, help="Pass the account ID explicitly")
    parser.add_argument("--duration-hours", required=False, default=None, help="Session duration in hours")
    parser.add_argument("--organizations-profile", required=False, default=None, help="AWS profile to use when gathering AWS organizations information")
    parser.add_argument("--source-profile", required=False, default=None, help="AWS profile to use when assuming a role")

    args = parser.parse_args()
    if args.help:
        parser.print_help(sys.stderr)
        sys.exit(0)
    config_filename = os.path.join(os.path.expanduser("~"), ".awslaunch.yaml")
    config = {}
    if os.path.exists(config_filename):
        with open(config_filename, "r") as f:
            config = ruyaml.safe_load(f)
    sys.exit(main(config=config, args=args))
