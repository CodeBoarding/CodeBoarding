import argparse
from pathlib import Path
from codeboarding_cli.commands import serve_analysis


def test_add_arguments_registers_serve():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command")
    shared = argparse.ArgumentParser(add_help=False)
    shared.add_argument("--local", type=Path)
    serve_analysis.add_arguments(subparsers, parents=[shared])
    args = parser.parse_args(["serve", "--local", "/tmp/x", "--port", "9999"])
    assert args.command == "serve"
    assert args.port == 9999
    assert args.host == "127.0.0.1"


def test_serve_in_main_subcommands():
    import main

    assert "serve" in main._SUBCOMMANDS
