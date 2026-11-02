"""Compatibility build configuration for older pip/setuptools combinations."""
from setuptools import setup


setup(
    name="gpu-monitor",
    version="0.1.0",
    description="A local, read-only NVIDIA GPU dashboard for SSH-reachable servers",
    long_description=open("README.md", encoding="utf-8").read(),
    long_description_content_type="text/markdown",
    python_requires=">=3.9",
    license="MIT",
    author="GPU Monitor Authors",
    py_modules=["server"],
    packages=["scripts", "web"],
    package_data={"web": ["*.css", "*.html", "*.js", "*.svg"]},
    entry_points={"console_scripts": ["gpu-monitor=scripts.manage:main"]},
)
