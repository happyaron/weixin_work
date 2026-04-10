from setuptools import setup, find_packages

setup(
    name="weixin-work",
    version="0.1.0",
    description="Convenient Python client for the WeCom (Weixin Work / 企业微信) messaging API",
    packages=find_packages(),
    python_requires=">=3.8",
    install_requires=["requests>=2.20"],
)
