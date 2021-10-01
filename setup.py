import os
import re
import pathlib
import setuptools

readme = pathlib.Path("README.md").read_text(encoding="utf-8")

__version__ = None
exec(open("jipdate/version.py").read())


def valid_requirement(req):
    return not (re.match(r"^\s*$", req) or re.match("^#", req))


requirements_txt = open("requirements.txt").read().splitlines()
requirements = [req for req in requirements_txt if valid_requirement(req)]
if os.getenv("REQ_IGNORE_VERSIONS"):
    requirements = [req.split(">=")[0] for req in requirements]

setuptools.setup(
    name="jipdate",
    version=__version__,
    description="Create and update Jira issues from the commandline",
    license="MIT",
    long_description=readme,
    long_description_content_type="text/markdown",
    url="https://github.com/linaro/jipdate.git",
    author="Joakim Bech",
    author_email="joakim.becn@linaro.org",
    packages=["jipdate"],
    include_package_data=True,
    python_requires=">=3.6, <4",
    install_requires=requirements,
    entry_points={
        "console_scripts": [
            "jipfp=jipdate.jipfp:main",
            "jipdate=jipdate.jipdate:main",
            "jipstatus=jipdate.jipstatus:main",
        ]
    },
)
