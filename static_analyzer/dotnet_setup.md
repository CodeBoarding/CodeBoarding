# C# Analysis Setup

CodeBoarding uses [csharp-ls](https://github.com/razzmatazz/csharp-language-server) (a Roslyn-based language server) for C# analysis. It is installed automatically by `codeboarding-setup` via `dotnet tool install`, but **requires the .NET 9.0 SDK** on the host machine.

## Requirements

| Component | Version | Why |
|-----------|---------|-----|
| .NET SDK | **9.0** | csharp-ls 0.20.0 targets `net9.0`; `Microsoft.Build.Locator` probes for a matching SDK to find MSBuild. A 10.0-only install is **not sufficient**. |

The 9.0 SDK includes the 9.0 runtime — no separate runtime install is needed.

If `dotnet` is not found, `codeboarding-setup` prints a warning and skips C# support. All other languages continue to work.

## Installation

### Linux

```bash
curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin --channel 9.0
```

Add to your shell profile (`~/.bashrc` or `~/.zshrc`):

```bash
export DOTNET_ROOT="$HOME/.dotnet"
export PATH="$HOME/.dotnet:$PATH"
```

Then reload:

```bash
source ~/.zshrc   # or ~/.bashrc
```

### macOS

```bash
# Via Homebrew (recommended)
brew install dotnet-sdk@9
```

Or use the install script (same as Linux):

```bash
curl -sSL https://dot.net/v1/dotnet-install.sh | bash /dev/stdin --channel 9.0
```

For shell profile, same as Linux above.

> **Apple Silicon note:** The Homebrew `dotnet` binary lives under `/opt/homebrew/Cellar/dotnet/<version>/libexec/`. CodeBoarding's adapter resolves `DOTNET_ROOT` automatically for this layout.

### Windows

**Option A — Winget:**

```powershell
winget install Microsoft.DotNet.SDK.9
```

**Option B — Manual download:**

Download the installer from <https://dotnet.microsoft.com/download/dotnet/9.0>.

The Windows installer adds `dotnet` to PATH and sets `DOTNET_ROOT` system-wide automatically.

## Verification

```bash
dotnet --list-sdks
# Should show:
# 9.0.xxx

dotnet --list-runtimes
# Should show:
# Microsoft.NETCore.App 9.0.x
```

Then run the CodeBoarding setup to install `csharp-ls`:

```bash
codeboarding-setup
```

Or if using the Python API:

```bash
python install.py --auto-install-npm
```

Verify C# support is active:

```
# The setup output should include:
#   csharp-ls: installed
#   - csharp: yes
```

## Troubleshooting

### "No instances of MSBuild could be detected"

csharp-ls cannot find the .NET SDK. Fixes:

1. Ensure the .NET **9.0 SDK** is installed (not just the runtime):
   ```bash
   dotnet --list-sdks
   ```
2. Ensure `DOTNET_ROOT` is set:
   ```bash
   echo $DOTNET_ROOT
   # Should print the SDK directory, e.g. /home/you/.dotnet
   ```
3. Ensure `dotnet` is on `PATH`:
   ```bash
   which dotnet
   ```

### "LSP server process exited with code 131"

csharp-ls crashed during startup, typically because the .NET runtime couldn't be found. Same fix as above — ensure `DOTNET_ROOT` and `PATH` are configured.

### csharp-ls install skipped during setup

The `dotnet` CLI was not on PATH when `codeboarding-setup` ran. Install the SDK first, restart your shell, then re-run setup.
