from setuptools import setup, find_packages

setup(
    name="vi_api_client",
    version="0.1.0",
    description="Async Python client for Viessmann Climate Solutions API",
    author="Michael",
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    install_requires=[
        "aiohttp>=3.8.1",
        "pkce>=1.0.3",
    ],
    entry_points={
        "console_scripts": [
            "vi_api_client=vi_api_client.cli:main",
        ],
    },
    python_requires=">=3.9",
    classifiers=[
        "Development Status :: 3 - Alpha",
        "Intended Audience :: Developers",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Framework :: AsyncIO",
    ],
)
