export const theme = {
  red: "#DB0011",
  ink: "#161616",
  paper: "#F5F4F1",
  white: "#FFFFFF",
  muted: "#6C6C68",
  line: "#D7D5CF",
  paleRed: "#FBE8E8",
  font: "Inter, Arial, Helvetica, sans-serif",
};

export const chapters = [
  { start: 0, end: 25, name: "The business loop" },
  { start: 25, end: 55, name: "A Duet for every person" },
  { start: 55, end: 95, name: "From signal to decision" },
  { start: 95, end: 125, name: "The message center" },
  { start: 125, end: 175, name: "Connected delivery" },
  { start: 175, end: 215, name: "Learning through experience" },
  { start: 215, end: 240, name: "An AI-native organisation" },
] as const;
