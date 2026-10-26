"""Command line: `crawler <command>`."""
from __future__ import annotations

from pathlib import Path

import click


@click.group()
def main() -> None:
    """Site-tuned UI crawler: intent-level flows in, short verified XPaths out."""


def _denylist(deny: tuple[str, ...], deny_file: str | None) -> list[str]:
    terms = [t.strip() for d in deny for t in d.split(",") if t.strip()]
    if deny_file:
        terms += [ln.strip() for ln in Path(deny_file).read_text().splitlines()
                  if ln.strip() and not ln.lstrip().startswith("#")]
    return terms


@main.command()
@click.argument("src", type=click.Path(exists=True, file_okay=False), required=False)
@click.option("--out", default="framework-profile", show_default=True, type=click.Path(file_okay=False),
              help="Where to write the redacted bundle.")
@click.option("--deny", multiple=True, metavar="TERMS",
              help="Comma-separated private terms to scrub (company/product names, domains). Repeatable.")
@click.option("--deny-file", type=click.Path(exists=True, dir_okay=False),
              help="File with one private term per line.")
@click.option("--package-prefix", default="com.example", show_default=True,
              help="Your first-party Java packages are renamed to this.")
@click.option("--keep-identifiers", is_flag=True,
              help="Keep your class/field/method names (default: domain words are replaced with generic stems).")
@click.option("--check", "check_only", type=click.Path(exists=True, file_okay=False),
              help="Only re-scan an existing bundle for leaks.")
def distill(src, out, deny, deny_file, package_prefix, keep_identifiers, check_only) -> None:
    """Make a redacted profile of your UI automation framework. Runs offline; nothing leaves your machine.

    SRC is the root of your Selenium/Cucumber repo. Review the bundle before sharing it.
    """
    from crawler.distill import check, distill as run_distill

    terms = _denylist(deny, deny_file)
    if check_only:
        problems = check(Path(check_only), terms)
        if problems:
            for p in problems:
                click.echo(f"LEAK  {p}", err=True)
            raise SystemExit(1)
        click.echo(f"clean: {check_only}")
        return
    if not src:
        raise click.UsageError("SRC is required unless --check is given.")
    if not terms:
        click.echo("warning: no --deny terms given; add your company/product names and domains.", err=True)
    bundle = run_distill(Path(src), Path(out), terms, rename_packages_to=package_prefix,
                         keep_identifiers=keep_identifiers)
    click.echo(f"wrote {bundle}  (open PROFILE.md first; review everything before sharing)")


if __name__ == "__main__":
    main()
