#!/bin/bash
set -eux

python_executable=python$1

# Install requirements
$python_executable -m pip install -r requirements-rocm.txt 
$python_executable setup.py clean --all
if [ ${USE_CYTHON} -eq "1" ]; then python3 setup_cython.py build_ext --inplace; fi

# Limit the number of parallel jobs to avoid OOM
export MAX_JOBS=1

bash tools/check_repo.sh

# Build
$python_executable setup.py bdist_wheel --dist-dir=dist
