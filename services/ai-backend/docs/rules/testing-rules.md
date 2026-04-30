# Testing Rules

## Required Tests

- Every feature implementation must include unit tests.
- Every Pydantic contract must have valid and invalid parse tests.
- Every registry must test listing, lookup, duplicate handling, disabled entries, and permission filtering.
- Every loader must test success, unknown name, unauthorized name, malformed schema, and external failure.
- Every subagent flow must test compact handoff and malformed result handling.

## Edge Cases

Edge cases are not optional. Use `docs/testing/edge-case-matrix.md` as the minimum list and add new cases when implementation discovers them.

## Test Isolation

- Use fakes over live services.
- Avoid broad integration tests for behavior that a unit test can prove.
- No real credentials in tests.
- No network dependency in unit tests.

## Failure Assertions

Tests must assert typed errors and safe messages. A test that only asserts an exception was raised is not enough for core runtime features.

## Regression Tests

Any bug fix in dynamic loading, permissions, context compression, memory routing, subagent lifecycle, or streaming must add a regression test.
