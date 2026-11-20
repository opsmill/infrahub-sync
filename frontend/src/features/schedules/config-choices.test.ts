import { describe, expect, it } from "vitest";

import { configVersionChoices, configVersionKey, parseConfigVersionKey } from "./config-choices";

describe("configVersionChoices", () => {
  it("lists every version, the latest first and marked, without the identifier", () => {
    expect(
      configVersionChoices([
        { config_id: "20261120T101500Z-a1", name: "from-netbox", latest_version: 3 },
      ])
    ).toEqual([
      {
        id: "20261120T101500Z-a1@3",
        label: "from-netbox, version 3, the latest",
        detail: undefined,
      },
      { id: "20261120T101500Z-a1@2", label: "from-netbox, version 2", detail: undefined },
      { id: "20261120T101500Z-a1@1", label: "from-netbox, version 1", detail: undefined },
    ]);
  });

  it("shows the identifier on every version of a configuration whose name another one shares", () => {
    expect(
      configVersionChoices([
        { config_id: "c-1", name: "from-netbox", latest_version: 2 },
        { config_id: "c-2", name: "from-netbox", latest_version: 1 },
        { config_id: "c-3", name: "from-nautobot", latest_version: 1 },
      ])
    ).toEqual([
      { id: "c-1@2", label: "from-netbox, version 2, the latest", detail: "c-1" },
      { id: "c-1@1", label: "from-netbox, version 1", detail: "c-1" },
      { id: "c-2@1", label: "from-netbox, version 1, the latest", detail: "c-2" },
      { id: "c-3@1", label: "from-nautobot, version 1, the latest", detail: undefined },
    ]);
  });

  it("names a configuration without a name by its identifier", () => {
    expect(configVersionChoices([{ config_id: "c-1", name: null, latest_version: 1 }])).toEqual([
      { id: "c-1@1", label: "c-1, version 1, the latest", detail: undefined },
    ]);
  });

  it("offers no choice for a configuration with no registered version", () => {
    expect(
      configVersionChoices([{ config_id: "c-1", name: "from-netbox", latest_version: null }])
    ).toEqual([]);
  });
});

describe("parseConfigVersionKey", () => {
  it("reads back the configuration and the version of a key", () => {
    expect(parseConfigVersionKey(configVersionKey("20261120T1249-9d57f620", 12))).toEqual({
      configId: "20261120T1249-9d57f620",
      version: 12,
    });
  });

  it.each(["c-1", "@3", "c-1@", "c-1@0", "c-1@x", "c-1@2.5"])("refuses %s", (key) => {
    expect(parseConfigVersionKey(key)).toBeUndefined();
  });
});
