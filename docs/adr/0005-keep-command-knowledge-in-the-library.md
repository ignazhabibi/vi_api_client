---
status: proposed
---

# Keep command knowledge in the library behind two write operations

Version 7.0.0 reshapes writing in one step, without a deprecation phase.
Consumers write through two operations. `set_feature` writes a value and
returns the command-updated device snapshot. `run_command(device,
feature_name, command_name, parameters)` sends any command an API feature
reports, addressed by names, and returns nothing. `execute_command` and
`CommandResponse` are removed. A command the device rejects raises
`ViCommandRejectedError`, so success always means that no exception was raised.

How a command is sent becomes internal. `Feature.control` and `FeatureControl`
give way to `Feature.constraints` and `FeatureConstraints`, which carry only
what a consumer needs to offer a value: type, range, step, options, text rules,
and schedule rules. Command names, parameter names, required parameters, and
URIs leave the public model. The snapshot lists the command names per API
feature. The consumer in this project's ecosystem reads none of the removed
fields.

`set_feature` also writes boolean features whose API feature offers an on/off
command pair instead of a parameter setter, such as `enable` and `disable` for
`heating.dhw.hygiene.enabled`. Which pairs count is an explicit table of
command families in the library: the one-time charge, hygiene, the heating
programs `comfort`, `eco`, and `forcedLastFromSchedule`, and the ventilation
quick modes. An entry applies when the property is a reported boolean, both
commands are reported without required parameters, and no parameter setter
targets the property; a setter such as `setEnabled` always wins.

Knowing how a Viessmann device expects a value to be written is the library's
job. Without the table every consumer repeats the same mapping. The heating
programs and quick modes in the table are offered only as pairs on every device
surveyed. Viessmann is replacing other pairs with parameter setters: the
Vitocal 250-A fixture reports `setEnabled` for hygiene and marks the pair as
deprecated, while other devices still offer only the pair, and the same holds
for `setActive` on the one-time charge. Both stay in the table as a fallback.
Because a setter always wins, such a device later changes its command without
changing whether the feature is writable.

Only a command whose name starts with `set` binds a value to a feature. Other
commands also take parameters that match properties, but they do more than
store a value: `triggerDaily` turns a weekly hygiene run into a daily one,
`activate` on the daylight saving feature switches it on, and `schedule` plans
a holiday. Writing one of their parameters through `set_feature` had effects
the caller did not ask for. These commands stay reachable through
`run_command`, where the caller names them. This prefix is the only place
where a command name decides something.

The table is explicit because a naming rule is not reliable.
`heating.dhw.hygiene` reports `activate` without a counterpart, and the program
variants `normalHeating`, `reducedHeating`, and `comfortHeating` report a pair
that no surveyed device marks executable.

Reported executability is not a gate. The API reports for every command whether
it is executable at that moment. The library uses this only while parsing, to
decide whether a parameter setter makes a feature writable, as before. A
command pair makes a feature writable whenever it is reported: the flags of a
pair follow the device state, and a feature must not change between writable
and read-only from one refresh to the next. The library never refuses a
command because of the flag and lets the API decide. A snapshot
can be minutes old, and refusing locally would block a command the device
accepts after its state changed elsewhere. For the same reason the direction of
an on/off pair is not checked: for hygiene a device reports only the
state-changing command as executable, so the report describes the state before
a write. The snapshot does not keep the flag and offers no query for it. No
consumer needs one yet, a query can be added later without a breaking change,
and keeping the flag would make snapshots compare unequal when nothing but a
flag changed.

A rejection is recognized in two ways. The API specification provides a
successful response whose `success` flag is false. In practice a Vitocal 250-A
answers a non-executable command with status 400 and error type
`DEVICE_COMMUNICATION_ERROR`, the same envelope as an unreachable device, and
names the cause only in `extendedPayload.reason` as `COMMAND_NOT_EXECUTABLE`.
Viessmann documents that field as debugging information that may change. The
library reads it nevertheless, for this one value and for command requests
only, because nothing else separates a rejected command from an outage. When
the field is absent or different, the response is the device communication
error, so a change on Viessmann's side makes the message less precise but
breaks nothing. Responses with the documented error type
`DEVICE_COMMUNICATION_ERROR` raise `ViDeviceCommunicationError` instead of the
validation error their status code suggests, because an unreachable device is
not a mistake in the request. For every other error the reason is appended to
the message as text and not interpreted.

`run_command` updates no snapshot. A command without a target value leaves
nothing to apply locally, and its effect differs per device: a holiday that is
not scheduled reads as empty dates on some bundled fixtures and as
`2000-01-01` on others. The API shows a change only after a delay that is not
documented and may vary, so the library neither waits nor reads back. In line
with ADR 0002, the consumer shows optimistic state and lets its regular refresh
confirm it.

The cost is a breaking release that every consumer must adapt to, features
that become writable on devices without a parameter setter and features that
stop being writable because an action command wrote them, which consumers
deriving entity types from `is_writable` see as a change, a pair that a device
never allows and that therefore fails on every write, a table the library
maintains as Viessmann changes its commands, and reliance on one undocumented
field value.
