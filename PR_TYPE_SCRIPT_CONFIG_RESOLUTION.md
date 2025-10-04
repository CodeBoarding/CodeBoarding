# TypeScript Config Resolution for Monorepos

## Summary
This PR implements dynamic TypeScript config resolution for monorepo structures, allowing proper scanning of TypeScript files in multi-project setups.

## Problem
Previously, the TypeScript LSP integration only looked for environment setup at the root directory, preventing proper scanning of TypeScript files in monorepos with multiple projects.

## Solution

### 1. TypeScript Config Scanner (`typescript_scanner.py`)
- Recursively finds `tsconfig.json` and `jsconfig.json` files throughout the repository
- Skips `node_modules` directories automatically
- Validates that projects contain actual TypeScript/JavaScript files
- Handles UTF-8 BOM in JSON files using `utf-8-sig` encoding

### 2. Dynamic Client Creation (`__init__.py`)
- Modified `create_clients` function to detect multiple TypeScript projects
- Creates individual `TypeScriptClient` instances for each valid project directory
- Maintains backward compatibility with single-project repositories

### 3. Project-Specific Configuration (`typescript_client.py`)
- Each TypeScript client handles config files relative to its `project_path`
- Proper workspace folder management for multi-project setups
- Individual project initialization and validation

## Key Features
- **Recursive scanning**: Finds TypeScript projects anywhere in the repository structure
- **Project validation**: Ensures detected projects contain actual TypeScript/JavaScript files
- **Multiple project support**: Creates separate LSP clients for each TypeScript project
- **Node_modules exclusion**: Automatically skips dependency directories
- **Robust error handling**: Gracefully handles malformed config files

## Testing
- Verified with test monorepo structure containing multiple TypeScript projects
- Each project correctly gets its own dedicated TypeScript client
- UTF-8 BOM files are handled properly
- Backward compatibility maintained for single-project repositories

## Files Changed
- `static_analyzer/typescript_scanner.py` (new)
- `static_analyzer/__init__.py` (modified)
- `static_analyzer/lsp_client/typescript_client.py` (enhanced)

## Dependencies
- Added `pathspec` package for efficient file pattern matching

This implementation resolves the original issue where only root-level TypeScript configurations were considered, enabling proper TypeScript analysis in complex monorepo environments.