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
@click.option("--show-leaks", is_flag=True,
              help="If the leak check fails, print the flagged values here (local terminal only; don't paste them).")
@click.option("--check", "check_only", type=click.Path(exists=True, file_okay=False),
              help="Only re-scan an existing bundle for leaks.")
def distill(src, out, deny, deny_file, package_prefix, keep_identifiers, show_leaks, check_only) -> None:
    """Make a redacted profile of your UI automation framework. Runs offline; nothing leaves your machine.

    SRC is the root of your Selenium/Cucumber repo. Review the bundle before sharing it.
    """
    from crawler.distill import check, distill as run_distill
    from crawler.distill.check import explain
    from crawler.distill.core import DistillLeakError

    terms = _denylist(deny, deny_file)
    if check_only:
        problems = (explain if show_leaks else check)(Path(check_only), terms)
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
    try:
        bundle = run_distill(Path(src), Path(out), terms, rename_packages_to=package_prefix,
                             keep_identifiers=keep_identifiers)
    except DistillLeakError as e:
        click.echo("Leak check failed, so nothing was written. Flagged:", err=True)
        for line in (e.details if show_leaks else e.problems):
            click.echo(f"  {line}", err=True)
        if not show_leaks:
            click.echo("Re-run with --show-leaks to see the flagged values on this terminal "
                       "(don't paste those values anywhere; paste only the lines above).", err=True)
        raise SystemExit(2)
    click.echo(f"wrote {bundle}  (open PROFILE.md first; review everything before sharing)")


@main.command()
@click.argument("url")
@click.option("--out", default="site-profile", show_default=True, type=click.Path(file_okay=False))
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              help="site.yaml; suggestions are then only additions to it.")
@click.option("--deny", multiple=True, metavar="TERMS", help="Comma-separated private terms. Repeatable.")
@click.option("--deny-file", type=click.Path(exists=True, dir_okay=False))
@click.option("--max-pages", default=30, show_default=True)
@click.option("--no-auth", is_flag=True, help="Don't borrow a login (public pages only).")
def probe(url, out, config_path, deny, deny_file, max_pages, no_auth) -> None:
    """Read-only, headless crawl of your site that records only shapes and counts (no content).

    Borrows your login from Chrome started with --remote-debugging-port=9222 (log in there first).
    """
    from crawler.config import load_config
    from crawler.nav.connect import harvest_auth, storage_state_fresh
    from crawler.probe import probe as run_probe

    cfg = load_config(config_path)
    state = None
    if not no_auth:
        state = str(cfg.path(cfg.auth.storage_state))
        if not storage_state_fresh(state, cfg.auth.max_age_minutes):
            click.echo(f"borrowing login from Chrome at {cfg.cdp_endpoint} ...")
            harvest_auth(cfg.cdp_endpoint, state)
    bundle = run_probe(url, Path(out), storage_state=state, max_pages=max_pages,
                       denylist=_denylist(deny, deny_file), cfg=cfg if config_path else None)
    click.echo(f"wrote {bundle}  (open SITE-PROFILE.md; review before sharing)")


if __name__ == "__main__":
    main()
