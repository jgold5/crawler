<#
.SYNOPSIS
  Run a chosen subset of a Cucumber + TestNG suite (Maven) from PowerShell.

.DESCRIPTION
  Builds one `mvn test` command from friendly options, prints it, runs it, and returns Maven's exit code.
  Selection uses Cucumber's own system properties (Cucumber 6+/7):
    cucumber.filter.tags  cucumber.features  cucumber.filter.name  cucumber.plugin
  so it works with a TestNG runner (AbstractTestNGCucumberTests + @CucumberOptions) without code changes:
  system properties override @CucumberOptions.

.EXAMPLE
  .\run-tests.ps1 -Tags "@smoke and not @wip"
.EXAMPLE
  .\run-tests.ps1 -Feature "src/test/resources/features/positions.feature:42"     # one scenario by line
.EXAMPLE
  .\run-tests.ps1 -Name "Edit quantity" -Threads 4
.EXAMPLE
  .\run-tests.ps1 -RerunFailed                                                     # failures of the last run
.EXAMPLE
  .\run-tests.ps1 -Tags "@regression" -Props @{ env = "qa"; uiVersion = "aggrid" } -DryRun
#>
[CmdletBinding()]
param(
  # Cucumber tag expression, e.g. "@smoke and not @wip" or "@JIRA-123 or @JIRA-456"
  [string]$Tags,
  # Feature files/folders, optionally with :line (several allowed): "features/a.feature:12","features/b"
  [string[]]$Feature,
  # Scenario name regex (cucumber.filter.name)
  [string]$Name,
  # TestNG runner class to execute (Surefire -Dtest). Set your default once here.
  [string]$Runner = "",
  # Parallel scenarios: only if your runner overrides scenarios() with @DataProvider(parallel = true);
  # passed to TestNG as dataproviderthreadcount (check your pom's surefire config if it has no effect)
  [int]$Threads = 0,
  # Rerun only the scenarios listed in the rerun file of the previous run (written by this script)
  [switch]$RerunFailed,
  # Extra -D system properties your framework reads (environment, UI version, browser, ...)
  [hashtable]$Props = @{},
  # Maven profile(s), e.g. "qa"  (not named -Profile: that would shadow PowerShell's $PROFILE)
  [string[]]$MavenProfile,
  # Maven offline mode (no dependency downloads)
  [switch]$Offline,
  # Show the command without running it
  [switch]$DryRun,
  # Where the project's pom.xml is (default: current folder)
  [string]$ProjectDir = "."
)

$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $ProjectDir
if (-not (Test-Path "pom.xml")) { throw "No pom.xml in $(Get-Location). Use -ProjectDir <path to the test project>." }

# Prefer the project's Maven wrapper
$mvn = if (Test-Path ".\mvnw.cmd") { ".\mvnw.cmd" } else { "mvn" }

$rerunFile = "target/cucumber-rerun.txt"
$reportDir = "target/cucumber-report"
$argsList = New-Object System.Collections.Generic.List[string]
$argsList.Add("test")
if ($Offline) { $argsList.Add("-o") }
if ($MavenProfile) { $argsList.Add("-P" + ($MavenProfile -join ",")) }

if ($RerunFailed) {
  if (-not (Test-Path $rerunFile) -or -not (Get-Content $rerunFile -Raw).Trim()) {
    Write-Host "Nothing to rerun: $rerunFile is missing or empty (the last run had no failures)." -ForegroundColor Green
    exit 0
  }
  # The rerun file lists path.feature:line entries; Cucumber accepts them as features.
  $Feature = (Get-Content $rerunFile -Raw).Trim() -split "\s+"
  Copy-Item $rerunFile "$rerunFile.prev" -Force
}

if ($Tags)    { $argsList.Add("-Dcucumber.filter.tags=$Tags") }
if ($Name)    { $argsList.Add("-Dcucumber.filter.name=$Name") }
if ($Feature) { $argsList.Add("-Dcucumber.features=" + ($Feature -join ",")) }

# Reports + a rerun file for -RerunFailed next time. Cucumber adds property plugins to the ones in
# @CucumberOptions; if your runner already writes a rerun file elsewhere, point $rerunFile at it.
$argsList.Add("-Dcucumber.plugin=pretty,html:$reportDir/index.html,json:$reportDir/cucumber.json,rerun:$rerunFile")

if ($Runner)      { $argsList.Add("-Dtest=$Runner"); $argsList.Add("-Dsurefire.failIfNoSpecifiedTests=false") }
if ($Threads -gt 0) { $argsList.Add("-Ddataproviderthreadcount=$Threads") }
foreach ($k in $Props.Keys) { $argsList.Add("-D$k=$($Props[$k])") }

# Show the exact command (quoted the way PowerShell passes it)
$shown = ($argsList | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join " "
Write-Host "$mvn $shown" -ForegroundColor Cyan
if ($DryRun) { exit 0 }

$start = Get-Date
& $mvn @argsList
$code = $LASTEXITCODE
$secs = [int]((Get-Date) - $start).TotalSeconds

Write-Host ""
if (Test-Path "$reportDir/index.html") { Write-Host "Report: $(Resolve-Path "$reportDir/index.html")" }
if ((Test-Path $rerunFile) -and (Get-Content $rerunFile -Raw).Trim()) {
  $n = ((Get-Content $rerunFile -Raw).Trim() -split "\s+").Count
  Write-Host "$n failed scenario(s); rerun them with: .\run-tests.ps1 -RerunFailed" -ForegroundColor Yellow
}
Write-Host ("Finished in {0}s with exit code {1}" -f $secs, $code) -ForegroundColor ($(if ($code -eq 0) { "Green" } else { "Red" }))
exit $code
