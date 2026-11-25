# Polypbase internal user guide

This guide accompanies the release. It is not displayed in the application.

## Factual records

Each box can have at most one biological record per ISO week. A second
submission is rejected without changing anything, even on the same date and
with `0/0`. Use the correction action on the existing record; it uses PATCH
and preserves the audit trail. Week-only records without an exact date cannot
yet be imported or saved through the generic workflow.

Alerts have been retired and are no longer an active feature. Counts,
temperatures, trends, and deviations from a target are presented as facts,
without inferred operational or biological warnings. A drop in polyp count or
a temperature deviation does not generate an alert. There is no longer a
workflow for viewing or resolving alerts.

A value of `0` is a real measurement, distinct from missing data. The action
history and `AuditLog` entries remain active and are preserved, including past
actions related to alerts. Only the historical `Alert` storage is dormant;
retiring the feature does not delete its data.

## Deactivate a box

**Disable tracking** removes a box from active tracking without deleting its
records, lineage, or associated actions. An administrator can reactivate a
deactivated box. Prefer this action to permanent deletion.

## Moving a box and correcting a move

Ordinary moves between zones are limited to one per box per ISO week in the
Europe/Paris time zone. Initial assignment, qualification, and reactivation
are separate operations. The server checks the expected location and may ask
you to reload after a concurrent change.

**Last movement** appears below the latest record. The pencil icon turns the
banner into an inline editor; it does not create another movement. An
administrator or authorized technician can correct a movement at any time,
regardless of who recorded it, but Django validates the periods and chronology.
For a legacy event whose provenance is not established, only the notes can be
corrected. If there is a conflict, reload and review the details. Do not force
the change or silently resubmit it.

An entry into or exit from a location period in the history or chart is not,
by itself, proof of a physical move.

## View or add DNA evidence

The badge next to the box code shows whether a DNA evidence link has been
entered. Use the badge to view the link, who added it, and when. An
administrator or technician at the active institution can add, edit, or remove
the evidence. If there is a conflict, review the box details before retrying.
Removing the evidence leaves a trace in the history.

In **New box**, the DNA evidence link is optional. Enter an HTTP or HTTPS URL.
Polypbase does not verify the contents of the evidence or pass it on to the
species, strain, or child boxes.

## Administration on desktop

Administrators can access **Team**, **Box inventory**, **Species**,
**Laboratory configuration**, **Sending and receiving**, and
**Institution actions**. The former **Shared references** and **Institutions**
sections have been removed. A species already used by the institution offers
the same actions with or without a profile. Opening its details does not save
anything. Changing the name or WoRMS reference and creating a strain go through
their respective forms and server-side validation.

## Propose a donation of an entire box

In **Administration > Sending and receiving**, choose **Send**, then select a
box and the destination institution. Check the box's eligibility and history,
then confirm that you want to send the proposal. Both institutions can view
the proposal details in their respective lists. The box remains under the
source institution's control, and its history is not shared with the
recipient. There is no accept or cancel button. Only the source institution
can cancel, and only through the API.

Donation is the only type of shipment offered in the interface. The external
sharing screens are not accessible, and the former CSV transfer workflow has
been removed. CSV export for tracking records is still available.

## Roles

- **Administrator**: team, locations, species, donation proposals, and box
  deactivation and reactivation, subject to permissions at the active institution.
- **Technician**: view and enter lab data and perform authorized operations on
  boxes at their institution.
- **Read-only mode**: view data only; no changes allowed.

## Verification after an update

Validate the release locally with synthetic data: create and correct a record,
check zero values and the action history, move a box and correct the movement,
and check transplanting and CSV export. Do not test a retired workflow as though
it were active. After an explicitly authorized deployment, service checks must
not create scientific data solely for a smoke test. Verify that box and location
pages have no alert workflows or warnings inferred from measurements. Also
check the application on a tablet before going live.

## Find a box and enter a record

First choose the correct institution, then search for the box code or scan its
QR code. Verify the code and location on the box page before entering a record.
To correct an existing record, open its correction action rather than creating
another record. If there is a conflict, review the box details before retrying.

**New box** requires an existing strain, a location, an entry date, and an
initial count. The server assigns the code after confirmation. Do not start
again with a new draft while the outcome of the first submission is still
unknown.

## Read location and probe data

On the location page, distinguish between the target, observed measurements,
and aggregates. **Thermal control** shows the available measurements from the
last four hours. An empty chart means neither that the temperature is zero nor
that an outage has been confirmed. Condensed series are not individual readings.
Probes do not generate automatic alerts.

## Manage species and strains

In **Administration > Species**, select the institution's profile. For a new
species, search WoRMS and explicitly select the result, or use **Enter manually without WoRMS**.
On an existing profile, edit the name and reference under **Scientific name and WoRMS**.
A historical species record without a profile does not offer these actions:
opening the record does not create a profile.

**Add a strain** requires a country and its institutional code if needed.
Acquisition details, coordinates, and a descriptive name are optional. Django
assigns the strain number and final code. If the AAA code is missing or
inconsistent, creation is blocked rather than inventing a code.

For detailed rules and API limits, see the [documentation index](../INDEX.md).
