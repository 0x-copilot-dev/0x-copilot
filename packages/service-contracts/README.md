# Enterprise Service Contracts

Constants-only Python package for stable internal service contracts shared by
deployable Python services.

This package must not contain service auth logic, persistence behavior, route
handlers, clients, or business rules. Services own their own policy and import
only stable names from here.
