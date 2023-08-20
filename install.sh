#!/bin/bash

separator="--------------------------------------------------------------------------------------------------------------------------"


activate_python_virtual_env() {
    # Activate the python virtual environment, if it exists
    # If it doesn't exist, create it
    if [[ -z "${VIRTUAL_ENV}" ]]; then
        echo "$separator"
        echo "Activating virtual environment"
        source .env/bin/activate 2>/dev/null || {
            echo "$separator"
            echo "Virtual environment not found. Creating one."
            python3.11 -m venv .env
            source .env/bin/activate
            echo "Virtual environment created and activated"
        }
    else
        echo "$separator"
        echo "Virtual environment already activated"
        echo "$separator"
    fi
}

install_python_requirements() {
    echo "$separator"
    echo "Installing Python dependencies"
    echo "$separator"
    # check if requirements.in exists
    if [ -f requirements.in ]; then
        pip-compile -q --strip-extras --upgrade --generate-hashes requirements.in
    fi
    pip install -r requirements.txt | grep -v 'Requirement already satisfied'
}


execute_all() {
    activate_python_virtual_env
    install_python_requirements
}

if [ $# -eq 0 ]; then
    execute_all
    exit 0
fi
