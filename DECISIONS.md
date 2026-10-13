# DECISIONS.md  (about 150-200 words in total - plain language, your own words)

# Design Decisions

**1. Indexes.** I created a unique index on employee code to prevent duplicate employees and a unique compound index on employee code and date to prevent duplicate attendance records. I also added indexes on attendance date, status and date, department and joining date, and joining date. I avoided adding another attendance employee-code-only index because the compound index already starts with that field.

**2. Punch-in race.** If two identical punch-in requests arrive together, both may pass the initial employee check. The unique compound index allows only one attendance record for that employee and date. MongoDB rejects the other insert, and the API returns HTTP 409.

**3. Ties.** The leaderboard uses MongoDB's `$rank` based on total late minutes. Employees tied at the cutoff receive the same rank, so all employees with rank less than or equal to the limit are returned.

**4. Headcount.** The department summary starts with employees who joined by the end of the month, then uses `$lookup` and preserves employees with no matching attendance logs. This includes employees with zero logs.

**5. Scaling.** At 100 times the data volume, I would consider pre-aggregating daily attendance statistics and measuring query performance before adding more indexes.