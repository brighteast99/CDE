import sys

import setuptools

print(setuptools.find_packages())

setuptools.setup(
    name="cde_governor",
    version="1.0",
    packages=setuptools.find_packages(),
    python_requires=">=3.6",
    include_package_data=True,
    package_dir={"cde_governor": "cde_governor"},
)
