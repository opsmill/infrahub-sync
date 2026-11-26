import { describe, expect, it } from "vitest";

import { describeSystem } from "./systems";

describe("describeSystem", () => {
  it("returns the name of the stored credential that the block names", () => {
    const system = describeSystem({
      name: "netbox",
      credential: "netbox-token",
      settings: { url: "https://netbox.example.com" },
    });
    expect(system.credential).toBe("netbox-token");
    expect(system.label).toBe("NetBox");
  });

  it.each([[undefined], [""], ["   "]])("returns no credential for %j", (credential) => {
    expect(describeSystem({ name: "netbox", credential, settings: {} }).credential).toBeUndefined();
  });
});
