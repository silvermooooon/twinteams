# Create Custom Reports Filtered by Date and Status

## User story

As a manager, I want to create custom reports by choosing a date range and one or more statuses, so that I can review the records relevant to the period and states I need to analyze.

## Context / description

Provide managers with report criteria for a selected date range and status selection. Date filtering uses UTC. A report should include records that match the date criterion and any selected status. When multiple statuses are selected, a record matching any selected status is included; the date and status criteria are combined. The exact date field and boundary rules, status catalogue, report contents, and whether reports can be saved or exported remain product decisions and must be confirmed before implementation.

## Acceptance criteria

1. **Manager can set a date range**  
   **Given** a manager is creating a custom report  
   **When** they enter or select a start date and end date  
   **Then** the selected range is shown as part of the report criteria.

2. **Invalid date range is handled**  
   **Given** a manager enters a range that is invalid under the product's confirmed date rules, including an end date before the start date  
   **When** they attempt to run or create the report  
   **Then** the report is not run with that invalid range  
   **And** the manager is told how to correct the criteria.

3. **Manager can select one or more statuses**  
   **Given** a manager is creating a custom report  
   **When** they choose one or more available statuses  
   **Then** the selected statuses are shown in the report criteria.

4. **Date and status criteria are combined**  
   **Given** a manager has specified a date range and one or more statuses  
   **When** the report is generated  
   **Then** included records match the date-range criterion **and** match at least one selected status.

5. **Multiple statuses use any-match behavior**  
   **Given** a manager selects multiple statuses  
   **When** the report is generated  
   **Then** a record matching any one of the selected statuses can be included, provided it also matches the date criterion.

6. **Displayed criteria correspond to report results**  
   **Given** a report has been generated  
   **When** the manager reviews the report  
   **Then** the selected date range and statuses are identifiable as the criteria used for that report.

7. **Empty results are handled clearly**  
   **Given** no records match the selected date and status criteria  
   **When** the report is generated  
   **Then** the manager is shown a clear empty-results state rather than results that do not match the criteria.

## Assumptions / open questions

- Confirm which date field the date range filters (for example, created, submitted, or updated date); none is assumed here.
- UTC governs date filtering. Confirm whether start and end dates are inclusive and how date-only inputs map to UTC timestamps; also confirm the timezone used for display, if different from UTC.
- Confirm the available status values, their labels, and whether statuses are mutually exclusive or can change during report generation.
- Confirm whether selecting one or more statuses is required, and what (if anything) an empty status selection means.
- Confirm which managers/roles may create and view reports and what records their permissions allow them to see.
- Define the report fields, sorting, pagination/volume behavior, and any performance expectations.
- Confirm whether managers can save, name, edit, schedule, share, or export reports and which file formats are supported; this story does not assume those capabilities.
