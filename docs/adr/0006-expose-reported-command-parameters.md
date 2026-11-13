---
status: accepted
---

# Expose reported command parameters for run_command

ADR 0005 took parameter names and required parameters out of the public model
and left consumers the command names per API feature. That is enough for
`set_feature`, which knows how to write a feature, but not for `run_command`
when the caller must choose a parameter value. The ventilation level is the
case that shows it: the API sets it through `setLevel` on
`ventilation.operating.modes.permanent`, an API feature whose only property is
`active`, and reports the allowed levels only as the `enum` of the `level`
parameter. The current level is a different API feature. A writable flat
feature for the level would have to combine two API features, which would
interpret the API instead of passing it on.

`Device.get_command_parameters(api_feature_name, command_name)` therefore
returns the parameters of one reported command in API order as
`CommandParameter` values: the name, whether the API reports it as required,
its reported type, and its constraints in the same `FeatureConstraints` shape
features use. It returns `None` when the API feature did not report the
command, and an empty tuple for a command without parameters. The values are
the API report without interpretation; `run_command` validates against the
same data.

The rest of ADR 0005 stands. Command URIs, executability, and deprecation of a
command stay internal, and the library still decides how `set_feature` writes
a feature. `CommandParameter` becomes part of the public compatibility
contract, so a change of its fields is a breaking change from now on.
