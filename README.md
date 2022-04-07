# awslaunch

PoC for CLI tool to make launching into a bunch of different roles much easier

## Installation

Clone this repo and set up a venv

```shell
python -m virtualenv venv
. venv/bin/activate
python -m pip install -r requirements.txt
```

Add a shell function to your `.${SHELL}rc` to eval the output of the script. (replace the path to wherever you installed it)

```shell
awslaunch () {
  eval $($HOME/code/github.com/sapslaj/awslaunch/bin/awslaunch $@)
}
```

Re-source or open a new session and give it a go `awslaunch -h`.
