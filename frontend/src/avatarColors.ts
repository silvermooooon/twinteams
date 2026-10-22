/*!
 * Colorful avatar hash/order and light Background2/Foreground2 pairs adapted from Fluent UI.
 * https://github.com/microsoft/fluentui/blob/master/packages/react-components/react-avatar/library/src/components/Avatar/useAvatar.tsx
 * https://github.com/microsoft/fluentui/blob/master/packages/tokens/src/alias/lightColorPalette.ts
 * https://github.com/microsoft/fluentui/blob/master/packages/tokens/src/global/colors.ts
 *
 * MIT License
 *
 * Copyright (c) Microsoft Corporation.
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
 * copies of the Software, and to permit persons to whom the Software is
 * furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
 * AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
 * OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
 * SOFTWARE
 */

// Official avatar order; light Background2 = tint40, Foreground2 = shade30.
const avatarPalette = [
  { backgroundColor: '#d69ca5', color: '#420610' }, // darkRed
  { backgroundColor: '#eeacb2', color: '#6e0811' }, // cranberry
  { backgroundColor: '#f1bbbc', color: '#751d1f' }, // red
  { backgroundColor: '#efc4ad', color: '#712d09' }, // pumpkin
  { backgroundColor: '#ffddb3', color: '#8f4e00' }, // peach
  { backgroundColor: '#f9e2ae', color: '#835b00' }, // marigold
  { backgroundColor: '#ecdfa5', color: '#6c5700' }, // gold
  { backgroundColor: '#e0cea2', color: '#553e06' }, // brass
  { backgroundColor: '#ddc3b0', color: '#50301a' }, // brown
  { backgroundColor: '#bdd99b', color: '#294903' }, // forest
  { backgroundColor: '#a8f0cd', color: '#00723b' }, // seafoam
  { backgroundColor: '#9ad29a', color: '#063b06' }, // darkGreen
  { backgroundColor: '#a6e9ed', color: '#00666d' }, // lightTeal
  { backgroundColor: '#9bd9db', color: '#02494c' }, // teal
  { backgroundColor: '#94c8d4', color: '#00333f' }, // steel
  { backgroundColor: '#a9d3f2', color: '#004377' }, // blue
  { backgroundColor: '#9abfdc', color: '#002c4e' }, // royalBlue
  { backgroundColor: '#c8d1fa', color: '#2c3c85' }, // cornflower
  { backgroundColor: '#a3b2e8', color: '#001665' }, // navy
  { backgroundColor: '#d2ccf8', color: '#3f3682' }, // lavender
  { backgroundColor: '#c6b1de', color: '#341a51' }, // purple
  { backgroundColor: '#d9a7e0', color: '#4c0d55' }, // grape
  { backgroundColor: '#e6bfed', color: '#63276d' }, // lilac
  { backgroundColor: '#f7c0e3', color: '#80215d' }, // pink
  { backgroundColor: '#eca5d1', color: '#6b0043' }, // magenta
  { backgroundColor: '#d696c0', color: '#43002b' }, // plum
  { backgroundColor: '#d7d4d4', color: '#444241' }, // beige
  { backgroundColor: '#cecccb', color: '#343231' }, // mink
  { backgroundColor: '#cdd6d8', color: '#3b4447' }, // platinum
  { backgroundColor: '#bcc3c7', color: '#202427' }, // anchor
] as const;

export function defaultAvatarColor(name: string) {
  // Fluent's default name seed stays identical across every Avatar entry point.
  let hashCode = 0;
  for (let index = name.length - 1; index >= 0; index--) {
    const character = name.charCodeAt(index);
    const shift = index % 8;
    hashCode ^= (character << shift) + (character >> (8 - shift));
  }
  return avatarPalette[hashCode % avatarPalette.length];
}
