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
@click.option("--pages", "pages", multiple=True, metavar="GLOB",
              help="Also read page-definition files (.yaml/.yml/.json/.xml) matching GLOB, relative to SRC or absolute "
                   "(e.g. 'src/test/resources/pages/**/*.yaml'). Repeatable. Off by default; files named like "
                   "credentials/secrets/.env/.properties are never read.")
def distill(src, out, deny, deny_file, package_prefix, keep_identifiers, show_leaks, check_only, pages) -> None:
    """Make a redacted profile of your UI automation framework. Runs offline; nothing leaves your machine.

    SRC is the root of your Selenium/Cucumber repo. Review the bundle before sharing it.
    """
    from crawler.distill import check, distill as run_distill
    from crawler.distill.check import explain
    from crawler.distill.core import DistillLeakError
    from crawler.distill.pages import DistillWarning
    import warnings

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
    caught: list = []
    try:
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", DistillWarning)
            bundle = run_distill(Path(src), Path(out), terms, rename_packages_to=package_prefix,
                                 keep_identifiers=keep_identifiers, pages=list(pages))
    except DistillLeakError as e:
        _echo_warnings(caught, DistillWarning)
        click.echo("Leak check failed, so nothing was written. Flagged:", err=True)
        for line in (e.details if show_leaks else e.problems):
            click.echo(f"  {line}", err=True)
        if not show_leaks:
            click.echo("Re-run with --show-leaks to see the flagged values on this terminal "
                       "(don't paste those values anywhere; paste only the lines above).", err=True)
        raise SystemExit(2)
    _echo_warnings(caught, DistillWarning)
    click.echo(f"wrote {bundle}  (open PROFILE.md first; review everything before sharing)")


def _echo_warnings(caught, category) -> None:
    for w in caught or []:
        if issubclass(w.category, category):
            click.echo(f"warning: {w.message}", err=True)


@main.command()
@click.argument("url")
@click.option("--out", default="site-profile", show_default=True, type=click.Path(file_okay=False),
              help="Where to write site-profile.yaml, SITE-PROFILE.md and site.suggested.yaml.")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False),
              help="site.yaml; suggestions are then only additions to it.")
@click.option("--deny", multiple=True, metavar="TERMS", help="Comma-separated private terms. Repeatable.")
@click.option("--deny-file", type=click.Path(exists=True, dir_okay=False), help="File with one private term per line.")
@click.option("--max-pages", default=30, show_default=True, help="Stop after this many distinct page shapes.")
@click.option("--no-auth", is_flag=True, help="Don't borrow a login (public pages only).")
def probe(url, out, config_path, deny, deny_file, max_pages, no_auth) -> None:
    """Read-only, headless crawl of your site that records only shapes and counts (no content).

    Writes a redacted site-profile.yaml plus site.suggested.yaml (config additions for site.yaml).

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


@main.command()
@click.argument("flow", type=click.Path(exists=True, dir_okay=False))
@click.option("--repo", "repo_root", required=True, type=click.Path(exists=True, file_okay=False),
              help="The XML locator repository (a folder of version folders, or of page files).")
@click.option("--version", "version", default=None, help="UI version folder in the repository (e.g. aggrid).")
@click.option("--config", "config_path", type=click.Path(exists=True, dir_okay=False), help="site.yaml.")
@click.option("--out", default="author-out", show_default=True, type=click.Path(file_okay=False))
@click.option("--runs", default=2, show_default=True, type=click.IntRange(1, 10),
              help="Fresh sessions every proposed locator must verify in.")
@click.option("--no-auth", is_flag=True, help="Don't borrow a login (public pages only).")
@click.option("--apply", "do_apply", is_flag=True, help="Write the XML edits (default: only xml.diff).")
@click.option("--answer", "answers", multiple=True, metavar="LINE=N",
              help="Answer an ASK.md question: candidate N for line LINE (remembered in aliases.yaml). Repeatable.")
@click.option("--aliases", "aliases_path", type=click.Path(dir_okay=False),
              help="aliases.yaml (default: next to FLOW).")
@click.option("--headed", is_flag=True, help="Show the browser.")
@click.option("--navmap", "navmap_path", type=click.Path(exists=True, dir_okay=False),
              help="navmap.yaml from `crawler navmap`: `go to X` for a page without a url clicks its tab -> option "
                   "(default: site.yaml `navmap:`).")
def author(flow, repo_root, version, config_path, out, runs, no_auth, do_apply, answers, aliases_path, headed,
           navmap_path) -> None:
    """Run an English FLOW on the live app: write the owner's feature, verified XML edits and a report.

    Exit codes: 0 done, 1 a check failed or a line could not run, 2 English/usage errors, 3 a line needs
    your answer (see OUT/ASK.md).
    """
    from crawler.author import author as run_author

    import sys

    from crawler.author import author_command
    # the command as typed (every option, --aliases/--apply included), shell-quoted, for the "answer" hint
    command = author_command(sys.argv[sys.argv.index("author"):] if "author" in sys.argv else ["author", flow])
    res = run_author(flow, repo_root=repo_root, version=version, config=config_path, out_dir=out, runs=runs,
                     no_auth=no_auth, apply=do_apply, answers=list(answers), aliases=aliases_path,
                     headless=not headed, echo=click.echo, command=command, navmap=navmap_path)
    if res.errors:
        click.echo(f"{flow}: {len(res.errors)} problem(s); nothing was run:", err=True)
        for e in res.errors:
            click.echo(f"  {e}", err=True)
    raise SystemExit(res.exit_code)


@main.command()
@click.option("--repo", "repo_root", required=True, type=click.Path(exists=True, file_okay=False),
              help="The XML locator repository (a folder of version folders, or of page files).")
@click.option("--version", "version", default=None, help="UI version folder in the repository (e.g. aggrid).")
@click.option("--config", "config_path", required=True, type=click.Path(exists=True, dir_okay=False),
              help="site.yaml (base_url: the page with the tab bar).")
@click.option("--out", default="navmap-out", show_default=True, type=click.Path(file_okay=False))
@click.option("--tabs", default=None, metavar="\"A,B\"",
              help="Only these tabs (their labels, comma-separated); also picks the bar when several fit.")
@click.option("--runs", default=2, show_default=True, type=click.IntRange(1, 10),
              help="Fresh sessions every proposed locator must verify in.")
@click.option("--no-auth", is_flag=True, help="Don't borrow a login (public pages only).")
@click.option("--apply", "do_apply", is_flag=True, help="Write the XML edits (default: only xml.diff).")
@click.option("--headed", is_flag=True, help="Show the browser.")
def navmap(repo_root, version, config_path, out, tabs, runs, no_auth, do_apply, headed) -> None:
    """Map the app's tab bar -> (lazy) dropdown options -> pages, verified in fresh sessions.

    Writes OUT/navmap.yaml, report.md, xml.diff (tab/option entries: reuse / fix / new) and NAVIGATE.md (the
    navigate step for your framework). Exit codes: 0 done, 1 no tab bar / a run failed, 2 usage, 3 several tab
    bars fit (see OUT/ASK.md; answer with --tabs).
    """
    from crawler.navmap import navmap as run_navmap

    res = run_navmap(repo_root=repo_root, version=version, config=config_path, out_dir=out, runs=runs,
                     tabs=[t for t in (tabs or "").split(",") if t.strip()] or None, no_auth=no_auth,
                     apply=do_apply, headless=not headed, echo=click.echo)
    for e in res.errors:
        click.echo(f"error: {e}", err=True)
    raise SystemExit(res.exit_code)


if __name__ == "__main__":
    main()
