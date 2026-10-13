# Code Review

| # | Where (function / line) | What is wrong | How you'd notice it | How you fixed it |
|---|---|---|---|---|
| 1 | `compute_late_minutes` | Uses naive timestamps and does not explicitly convert to IST. | Test a UTC timestamp against an IST shift start. | Convert timestamps to IST before calculating lateness. |
| 2 | `compute_work_hours` | Python `round()` does not guarantee the required half-up rounding. | Test durations at rounding boundaries. | Use `Decimal` with `ROUND_HALF_UP`. |
| 3 | `compute_overtime` | Does not correctly handle overnight shifts or the 30-minute minimum. | Test an overnight shift and overtime below 30 minutes. | Handle next-day shift end and apply the minimum threshold. |
| 4 | `create_employee` | A check-then-insert sequence can allow duplicate employee codes under concurrency. | Send simultaneous requests with the same code. | Add a unique index and handle `DuplicateKeyError`. |
| 5 | `list_employees` | The original skip calculation skips the first page, and the total ignores the department filter. | Request page 1 and filter by department. | Use `(page - 1) * page_size` and count filtered employees. |
| 6 | `punch_in` | Does not safely handle missing employees or concurrent duplicate punches. | Punch in for an unknown employee or send simultaneous requests. | Validate the employee and rely on a unique compound index to reject duplicates. |
| 7 | `list_attendance` | Loads all matching documents into Python before sorting and paginating. | Test with a large attendance collection. | Apply database sorting, counting, skipping and limiting. |
| 8 | Missing endpoints | The starter does not implement punch-out, regularization, analytics, trend, leaderboard or explain endpoints. | Compare the starter routes with the API contract. | Implement the missing endpoints and required aggregation pipelines. |

## Reviewed but not considered defects

- The health endpoint returning a simple status response is reasonable, but checking MongoDB availability makes it more useful.
- Keeping work-hour, late-minute and overtime calculations in helper functions improves reuse and testability.
- A default page size of 20 is reasonable when pagination limits are validated.

