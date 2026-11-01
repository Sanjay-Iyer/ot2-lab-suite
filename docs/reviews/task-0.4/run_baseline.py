"""Historical worktree/copy runner retired by the single-branch instructions.

Task R0 preserves the old implementation and its evidence in commit 06c0bdd.
Task 0.4 must supply a guarded in-place runner before broader tests resume.
This entry point performs no checkout, copying, cleanup or test execution.
"""
import argparse


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    parser.error("Historical runner retired: the single-branch workflow forbids project copies/worktrees. In-place replacement is pending Task 0.4.")


if __name__ == "__main__":
    main()
