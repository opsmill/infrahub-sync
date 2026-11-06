import { Button, Menu, MenuItem, MenuTrigger, Popover } from "@infrahub/ui";
import type { ReactNode } from "react";

import { FOCUS_RAC } from "#/components/classes";
import { MonitorIcon, MoonIcon, SunIcon } from "#/components/icons";
import { type ResolvedTheme, setTheme, THEMES, type Theme, useTheme } from "#/lib/theme";

const LABEL: Record<Theme, string> = {
  system: "System setting",
  light: "Light",
  dark: "Dark",
};

const ICON: Record<Theme, ReactNode> = {
  system: <MonitorIcon className="size-4" />,
  light: <SunIcon className="size-4" />,
  dark: <MoonIcon className="size-4" />,
};

const PALETTE: Record<ResolvedTheme, string> = { light: "light", dark: "dark" };

function isTheme(key: unknown): key is Theme {
  return THEMES.includes(key as Theme);
}

/** Chooses light, dark or the system setting; the choice is kept in this browser. */
export function ThemeMenu() {
  const { theme, resolved, system } = useTheme();
  const label =
    theme === "system"
      ? `Theme: system setting, ${PALETTE[system]} now`
      : `Theme: ${PALETTE[resolved]}`;

  return (
    <MenuTrigger>
      <Button
        variant="ghost"
        size="sm"
        shape="square"
        aria-label={label}
        className={`relative shrink-0 text-foreground-muted before:absolute before:-inset-y-[7px] before:-right-[7px] before:-left-[5px] data-hovered:text-foreground pointer-coarse:before:-inset-y-2 pointer-coarse:before:-right-2 ${FOCUS_RAC}`}
      >
        {ICON[resolved]}
      </Button>
      <Popover placement="bottom end" className="bg-content">
        <Menu
          aria-label="Theme"
          variant="picker"
          selectionMode="single"
          disallowEmptySelection
          selectedKeys={[theme]}
          onSelectionChange={(keys) => {
            const [next] = keys === "all" ? [] : keys;
            if (isTheme(next)) setTheme(next);
          }}
          className="min-w-52"
        >
          {THEMES.map((id) => (
            <MenuItem
              key={id}
              id={id}
              textValue={LABEL[id]}
              aria-label={id === "system" ? `${LABEL[id]}, ${PALETTE[system]} now` : undefined}
              className="text-foreground pointer-coarse:min-h-11 [&_svg.lucide.lucide-check]:size-4 [&_svg.lucide-check]:[stroke-width:2.25]"
            >
              {ICON[id]}
              <span>{LABEL[id]}</span>
              {id === "system" && (
                <span className="text-foreground-muted text-xs">{PALETTE[system]} now</span>
              )}
            </MenuItem>
          ))}
        </Menu>
      </Popover>
    </MenuTrigger>
  );
}
