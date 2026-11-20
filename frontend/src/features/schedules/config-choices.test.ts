import { describe, expect, it } from "vitest";

import { configChoices } from "./config-choices";

describe("configChoices", () => {
  it("labels a configuration with its name and latest version, without its identifier", () => {
    expect(
      configChoices([{ config_id: "20261120T101500Z-a1", name: "from-netbox", latest_version: 3 }])
    ).toEqual([{ id: "20261120T101500Z-a1", label: "from-netbox, version 3", detail: undefined }]);
  });

  it("shows the identifier of each configuration whose name another one shares", () => {
    expect(
      configChoices([
        { config_id: "c-1", name: "from-netbox", latest_version: 3 },
        { config_id: "c-2", name: "from-netbox", latest_version: 1 },
        { config_id: "c-3", name: "from-nautobot", latest_version: 2 },
      ])
    ).toEqual([
      { id: "c-1", label: "from-netbox, version 3", detail: "c-1" },
      { id: "c-2", label: "from-netbox, version 1", detail: "c-2" },
      { id: "c-3", label: "from-nautobot, version 2", detail: undefined },
    ]);
  });

  it("names a configuration without a name by its identifier", () => {
    expect(configChoices([{ config_id: "c-1", name: null, latest_version: 2 }])).toEqual([
      { id: "c-1", label: "c-1, version 2", detail: undefined },
    ]);
  });

  it("leaves the version out when the summary has no latest version", () => {
    expect(
      configChoices([{ config_id: "c-1", name: "from-netbox", latest_version: null }])
    ).toEqual([{ id: "c-1", label: "from-netbox", detail: undefined }]);
  });
});
