#!/usr/bin/env bash

### IMPORTANT
### This script should be kept in sync among all close repos.
# If making any changes to it, please update it in other repos as well.

set -e

PIP_COMPILE_CMD="pip-compile --generate-hashes --allow-unsafe"

### helper functions

function find_project_root {
  project_root="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"  # start at script location
  while [[ ! -f "$project_root/.python-version" && "$project_root" != "/" ]]; do
    project_root=$(dirname "$project_root")
  done
  echo $project_root
}

function check_required_files_exist {
  if [[ ! -f "$(pwd)/.python-version" ]]; then
    echo "Cannot find .python-version file. You should put it in project root."
    exit 1
  fi
  if [[ ! -d "$(pwd)/requirements" ]]; then
    echo "Cannot find a 'requirements' directory."
    exit 1
  fi
}

function find_input_files {
  # read ls output into an array
  # The `sort -r` is needed here:
  # - in order to avoid conflicts with dependabot (which also sorts)
  # - because we must first generate a new `requirements.txt` so that
  #   it can be used as a constraint in `requirements-lint.in`.
  mapfile -t $1 <<< "$(cd requirements && ls *.in | sort -r)"
}

function run_in_docker {
  script_relative_path=$(python3 -c "import os; print(os.path.relpath('${BASH_SOURCE[0]}'))")
  SCRIPT=$(cat <<EOF
set -e
cd code
python3 -m venv /tmp/venv3
. /tmp/venv3/bin/activate
pip install --no-deps -r requirements/requirements-pip.txt
pip install $(grep -rwoh requirements -e 'pip-tools==.*[^\]' | head -n 1) || \
  ( echo -e \
    "\033[0;31mProblem installing pip-tools! Do you have pip-tools in one of the requirements files?\033[0m" && \
    exit 1 )
./$script_relative_path ${ORIGINAL_ARGS[@]}
EOF
)
  docker run \
    --user=$(id -u):$(id -g) \
    --rm \
    --env HOME=/code \
    --mount type=bind,source=$(pwd),target=/code \
    python:$(cat .python-version) \
    bash -c "$SCRIPT"
  exit $?
}

### main

cd $(find_project_root)
check_required_files_exist
find_input_files ALL_IN_FILES

## handle options
ORIGINAL_ARGS=("$@")
POSITIONAL_ARGS=()
while [[ $# -gt 0 ]]; do
  case $1 in
    -h|--help)
      echo "Usage: $0 [options from pip-compile] [src-file-1 src-file-2 ...]"
      echo "Compiles *.in requirements files into *.txt requirements files."
      echo "If no arguments are provided, all the *.in files are compiled:"
      printf '  %s\n' "${ALL_IN_FILES[@]}"
      exit 0
      ;;
    -*|--*)
      PASSTHROUGH_OPTIONS+=("$1 $2 ")
      shift
      shift
      ;;
    *)
      POSITIONAL_ARGS+=("$1")
      shift
      ;;
  esac
done
# end handle options

PYTHON_VERSION=$(set -o pipefail; python3 --version | cut -d' ' -f2 || echo "null")
PYTHON_VERSION_REQUIRED=$(cat .python-version)
SYS_PLATFORM=$(python3 -c 'import sys; print(sys.platform)' || echo "null")

if [[ $PYTHON_VERSION != $PYTHON_VERSION_REQUIRED || $SYS_PLATFORM != "linux" ]]; then
  echo "This script must be run on Python $PYTHON_VERSION_REQUIRED on Linux!"
  echo "You're running Python $PYTHON_VERSION on platform '$SYS_PLATFORM'"
  if [ -t 0 ]; then  # if running interactively
    read -p "Do you want to run it in a docker container? (y/n): " choice
    if [[ $choice == "y" || $choice == "Y" ]]; then
      run_in_docker
    else
      exit 1
    fi
  else
    exit 1
  fi
fi

if [ ${#POSITIONAL_ARGS[@]}  -eq 0 ]; then
  FILES_TO_COMPILE=("${ALL_IN_FILES[@]}")
else
  FILES_TO_COMPILE=("${POSITIONAL_ARGS[@]}")
fi

cd requirements
for in_file in "${FILES_TO_COMPILE[@]}"; do
    echo "Compiling $in_file..."
    eval "$PIP_COMPILE_CMD" "$PASSTHROUGH_OPTIONS" "$in_file"
done

