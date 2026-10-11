// display reference only: a colour and a name to recognise a team by, never data
const TEAMS: Record<string, { color: string; name: string }> = {
  mclaren: { color: "#ff8a1f", name: "McLaren" },
  ferrari: { color: "#ff3b5c", name: "Ferrari" },
  red_bull: { color: "#4f7dff", name: "Red Bull" },
  mercedes: { color: "#2ee6c5", name: "Mercedes" },
  aston_martin: { color: "#2fbf7f", name: "Aston Martin" },
  alpine: { color: "#ff7ac0", name: "Alpine" },
  williams: { color: "#58c4ff", name: "Williams" },
  rb: { color: "#8a8cff", name: "Racing Bulls" },
  alphatauri: { color: "#8a8cff", name: "AlphaTauri" },
  haas: { color: "#c8cbd0", name: "Haas" },
  audi: { color: "#d9d2c5", name: "Audi" },
  sauber: { color: "#52e252", name: "Sauber" },
  alfa: { color: "#c92d4b", name: "Alfa Romeo" },
  cadillac: { color: "#e6e1d6", name: "Cadillac" },
};

export function teamColor(id: string): string {
  return TEAMS[id]?.color ?? "#8e909a";
}

export function teamName(id: string): string {
  return TEAMS[id]?.name ?? id.replace(/_/g, " ");
}

const CIRCUITS: Record<string, string> = {
  albert_park: "Melbourne",
  americas: "Austin",
  bahrain: "Bahrain",
  baku: "Baku",
  catalunya: "Barcelona",
  hungaroring: "Hungaroring",
  imola: "Imola",
  interlagos: "Interlagos",
  jeddah: "Jeddah",
  losail: "Lusail",
  madring: "Madrid",
  marina_bay: "Marina Bay",
  miami: "Miami",
  monaco: "Monaco",
  monza: "Monza",
  red_bull_ring: "Spielberg",
  rodriguez: "Mexico City",
  sepang: "Sepang",
  shanghai: "Shanghai",
  silverstone: "Silverstone",
  spa: "Spa",
  suzuka: "Suzuka",
  vegas: "Las Vegas",
  villeneuve: "Montréal",
  yas_marina: "Yas Marina",
  zandvoort: "Zandvoort",
};

export function circuitName(id: string): string {
  return CIRCUITS[id] ?? id.replace(/_/g, " ").replace(/\b\w/g, (letter) => letter.toUpperCase());
}
