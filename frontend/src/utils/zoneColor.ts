export type ZoneColor = {
  background: string;
  border: string;
  text: string;
};

const ZONE_HUES = [193, 145, 42, 225, 286, 14, 168, 258, 78, 332, 116, 205];

export function getZoneColor(name: string): ZoneColor {
  const hue = ZONE_HUES[hashZoneName(name) % ZONE_HUES.length];
  return {
    background: `hsl(${hue} 42% 94% / 0.82)`,
    border: `hsl(${hue} 48% 48%)`,
    text: `hsl(${hue} 44% 27%)`,
  };
}

function hashZoneName(value: string) {
  let hash = 2_166_136_261;
  for (const character of value.trim().toLocaleLowerCase()) {
    hash ^= character.charCodeAt(0);
    hash = Math.imul(hash, 16_777_619);
  }
  return hash >>> 0;
}
