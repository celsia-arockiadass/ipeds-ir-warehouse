# IPEDS Institutional Research Data Warehouse

Every IPEDS submission has to reconcile, clear its edit checks, and be defensible when someone asks where a number came from. This repository builds the pipeline that makes that possible: raw federal data in, validated warehouse in the middle, submission-ready package out.

**Status: in progress.** See [Build status](#build-status) for what is complete and what is not. Nothing in this document describes work that has not been done.

---

## What this is

A dimensional data warehouse and analytics platform built on ten years of public IPEDS data covering all United States postsecondary institutions, running on SQL Server with a Python ingestion layer.

Concretely, it contains:

- **An ingestion pipeline** that downloads IPEDS complete data files, handles variable renaming across survey years, imputation and status flags, provisional versus revised releases, and the post-2010 race and ethnicity category changes. Re-runnable from zero with one command. Every rejected row is logged with a reason.
- **A star schema warehouse** in SQL Server, with a slowly changing institution dimension that tracks name changes, mergers, closures, and sector or control changes over time.
- **A validation engine** that mirrors published IPEDS edit checks: totals reconcile to their parts, cross-survey consistency, year-over-year change thresholds flagged for human review, required-field completeness, and referential integrity. Every load produces a data quality report.
- **Cohort analytics computed to federal methodology**, retention and graduation rates at 100, 150 and 200 percent of normal time, with the cohort definition and exclusions documented rather than assumed.
- **A simulated IPEDS submission package** generated from the warehouse, in the specified upload layout, clearing the edit checks, with a reconciliation memo tracing each reported figure back to source.

---

## Why it is built this way

Design decisions and the alternatives rejected are documented in [`docs/`](docs/). The short version:

| Decision | Why | What was rejected |
|---|---|---|
| SQL Server, not SQLite | Real T-SQL, stored procedures, indexed views | SQLite, which cannot demonstrate any of that |
| Enterprise Developer edition | Automatic indexed view matching | Standard, which needs a `NOEXPAND` hint; Express, which caps a database at 10 GB |
| Star schema | Matches the slice-and-aggregate query pattern IR offices actually have | Third normal form, which reduces redundancy at the cost of many more joins per question |
| Staging layer before the warehouse | A bad load is diagnosed by querying staging, not by opening a CSV | Loading CSVs straight into fact tables, faster to write and much harder to debug |
| Python orchestration | Portable, version controlled, reviewable | SSIS, which is not available here. See Scope and limitations |

---

## Data sources

All public, all free, all real. No synthetic or sample data is used anywhere in this project.

- **IPEDS Complete Data Files**, National Center for Education Statistics. https://nces.ed.gov/ipeds/use-the-data
- **College Scorecard**, US Department of Education. https://collegescorecard.ed.gov/data
- **NCES Digest of Education Statistics**, for national benchmarks

---

## Scope and limitations

Read this section before drawing conclusions from anything here.

- **This is aggregate institution-level data, not student-level records.** IPEDS is published as institutional aggregates. Analyses that would require unit records, individual student pathways, term-to-term stop-out patterns, are not possible with this source and are not attempted.
- **No ERP or student information system access.** This project was built without Banner, Colleague, Workday or PeopleSoft. The staging tables for enrollment are deliberately shaped to mirror a Banner ODS extract, using Ellucian's published data model documentation, so the transformation logic would carry over. That is a design exercise, not operational experience with the system.
- **Orchestration is Python and T-SQL, not SSIS.** Where an institution running SQL Server would likely use SSIS packages, this implements the equivalent pattern in Python and stored procedures. The pattern is the same; the tool is not.
- **The submission package is a simulation.** It is generated in the published upload layout and validated against edit checks reimplemented from IPEDS documentation. It has not been submitted to the IPEDS Data Collection System, because only a keyholder at a participating institution can do that.
- **Validation rules are a reimplementation, not the official ones.** They are written from published IPEDS edit check documentation. They are not the government's code and may differ in detail.
- **Statistical work is in R and SAS.** SAS runs in SAS OnDemand for Academics, the free cloud environment, not an enterprise SAS installation.

Anywhere a computed figure differs from an IPEDS published figure, the difference is documented rather than reconciled away.

---

## Repository layout

```
config/          Download manifest and pipeline configuration
data/            Raw and interim data (not committed, rebuilt by the pipeline)
docs/            Methodology, data dictionary, governance, design decisions
powerbi/         Dashboard file and walkthrough
r/               Statistical analysis
reports/         Generated data quality and analysis output
sql/ddl/         Table definitions
sql/procs/       Stored procedures
sql/views/       Views and indexed views
src/ingest/      Download and staging load
src/warehouse/   Dimensional model load
src/validation/  Edit check engine
src/analytics/   Cohort and equity analysis
src/forecast/    Enrollment projection
src/submission/  IPEDS submission package generation
tests/           Environment and pipeline tests
```

---

## Running it

To be written once the pipeline is complete. It will be one command from a clean clone.

---

## Build status

- [x] Environment and warehouse database
- [ ] Data acquisition
- [ ] Ingestion and staging
- [ ] Star schema
- [ ] Validation engine
- [ ] Cohort analytics
- [ ] Statistical analysis
- [ ] Enrollment forecast
- [ ] Submission package
- [ ] Power BI dashboard
- [ ] Documentation and data dictionary

---

## Author

Celsia Arockiadass
Master of Data Analytics, New Mexico State University
https://linkedin.com/in/celsia-arockiadass-6707b0220
