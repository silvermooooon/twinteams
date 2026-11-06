import { loadFont } from "@remotion/fonts";
import { staticFile } from "remotion";

for (const weight of ["400", "500", "600", "700"]) {
  void loadFont({
    family: "Inter",
    url: staticFile(`fonts/inter-latin-${weight}-normal.woff2`),
    weight,
    display: "block",
  });
}
