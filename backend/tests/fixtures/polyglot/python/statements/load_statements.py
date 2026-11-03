"""Nightly statement load, run by scripts/nightly_digital.sh."""
from statements.service import StatementService


def main():
    StatementService().build("ALL", "current")


if __name__ == "__main__":
    main()
