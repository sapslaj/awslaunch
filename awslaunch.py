import argparse
import configparser
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
    "save": "Save the role to an AWS shared profile",
    "url": "Print the Switch Role URL",
    "role": "Print the role ARN",
}


def cmd(*inputs):
    print(*inputs, end=CMD_END)


def echo(*s):
    cmd("echo ", *s)


def msg(*s):
    print(*s, file=sys.stderr)


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


def generate_session_name(sts_client, config, default="awslaunch"):
    session_name = config.get("role_session_name")
    if session_name:
        return session_name
    try:
        caller_arn = sts_client.get_caller_identity()["Arn"]
        return re.findall(r".+/(.+)", caller_arn)[0]
    except:
        return default


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
    return [
        f'export AWS_ACCESS_KEY_ID="{credentials["AccessKeyId"]}"',
        f'export AWS_SECRET_ACCESS_KEY="{credentials["SecretAccessKey"]}"',
        f'export AWS_SESSION_TOKEN="{credentials["SessionToken"]}"',
    ]


def generate_save_profile_name(account_display_name, role_name):
    return re.sub(r"[!@#$%^&\*\(\)\[\]\{\};:\,\./<>\?\|`~=_+ ]", "-", f"{account_display_name}-{role_name}".lower())


def choose_account(account_choices, account_id=None):
    if account_id:
        return [account for account in account_choices.values() if account["Id"] == account_id][0]
    fzf = FzfPrompt()
    choice = fzf.prompt(sorted(account_choices.keys()))
    return account_choices[choice[0]]


def choose_role_name(role_map, account_id, args):
    if args.role_name:
        return args.role_name
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


def choose_save_profile_name(args, default="default"):
    if args.save_profile_name:
        return args.save_profile_name
    sys.stderr.write(f"Enter the profile name to save [{default}]: ")
    return input() or default


def save_profile(source_profile, role_arn, session_name, profile_name):
    aws_config_path = os.path.join(os.path.expanduser("~"), ".aws", "config")
    aws_config = configparser.ConfigParser()
    aws_config.read(aws_config_path)
    section = f"profile {profile_name}"
    aws_config.add_section(section)
    aws_config[section] = aws_config[f"profile {source_profile}"]
    aws_config[section]["source_profile"] = source_profile
    aws_config[section]["role_session_name"] = session_name
    aws_config[section]["role_arn"] = role_arn
    with open(aws_config_path, "w") as f:
        aws_config.write(f)



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
    role_name = choose_role_name(
        account_id=account_id,
        role_map=config.get("roles", {}),
        args=args,
    )
    role_arn = generate_role_arn(account_id=account_id, role_name=role_name)
    url = generate_url(role_name=role_name, account_id=account_id, display_name=account_display_name)
    actions = choose_actions(args)

    if not actions:
        raise Exception(f"Oh dang this should never happen, action is {actions}")
    if "assume" in actions:
        session_name = generate_session_name(sts_client=sts_client, config=config)
        duration_hours = int(args.duration_hours or config.get("duration_hours", 1))
        session_credentials_commands = generate_session_credentials_commands(
            sts_client=sts_client,
            role_arn=role_arn,
            session_name=session_name,
            duration_hours=duration_hours,
        )
        for command in session_credentials_commands:
            cmd(command)
        msg(f"'{role_arn}' from '{account_display_name}' assumed.")
    if "browser" in actions:
        msg(f"opening browser to '{url}'")
        webbrowser.open(url)
    if "save" in actions:
        session_name = generate_session_name(sts_client=sts_client, config=config)
        msg("saving role assume to an AWS profile")
        msg(f"Source profile: {source_profile}")
        msg(f"Account name: {account_display_name}")
        msg(f"Role ARN: {role_arn}")
        msg(f"Role Session Name: {session_name}")
        default_save_profile_name = generate_save_profile_name(account_display_name=account_display_name, role_name=role_name)
        save_profile_name = choose_save_profile_name(args=args, default=default_save_profile_name)
        msg(f"Profile Name: {save_profile_name}")
        save_profile(
            source_profile=source_profile, role_arn=role_arn, session_name=session_name, profile_name=save_profile_name
        )
        msg(f"Profile saved. Use `--profile {save_profile_name}` or `AWS_PROFILE={save_profile_name}` to use it")
    if "url" in actions:
        echo(f"'{url}'")
    if "role" in actions:
        echo(f"'{role_arn}'")
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
    parser.add_argument(
        "--organizations-profile",
        required=False,
        default=None,
        help="AWS profile to use when gathering AWS organizations information",
    )
    parser.add_argument(
        "--source-profile", required=False, default=None, help="AWS profile to use when assuming a role"
    )
    parser.add_argument(
        "--save-profile-name", required=False, default=None, help="Profile name to save when using --save"
    )

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
