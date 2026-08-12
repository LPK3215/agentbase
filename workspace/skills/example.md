# Example Skill: Code Review Checklist

## Purpose
This skill provides a checklist for reviewing code changes before approval.

## Trigger Conditions
- When the agent is asked to review a file or diff
- When the agent is about to write to a source file

## Recommended Usage
1. Read the target file using `read_file`
2. Check for: syntax errors, unused imports, missing error handling, hardcoded secrets
3. Report findings as a structured list

## Example Input
```
Review the file src/agentbase/cli.py for common issues.
```

## Example Output
```
- [OK] No syntax errors
- [WARN] Unused import: json (line 5)
- [OK] Error handling present in main()
- [OK] No hardcoded secrets detected
```