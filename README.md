### OSGAR: .osr and .svg .gcode generator.

The goal of the project was to originally have a svg gcode generator for pen plotting with the Neptune 4 Max, eventually adding a parser for .osr files. 
To Do: 
- Test with CC1
- Optimize for drag detection
- Make a start stop auto eject .gcode generator.



### 0. Use and Application
---

Example of it being compared to raw gameplay, and compared to what the parser had outputted. Clearly, not one to one, room for improvement.
Yellow is raw gameplay, and Blue is .gcode output running in Osu! Lazer.

https://github.com/user-attachments/assets/0f2c4392-2177-4e04-8eeb-399015de87d0

---

Example of use in real world, having the exact same .gcode file in simulated gameplay allowed this project to happen. 
This was comparing the pulling rate of stock 133hz compared to the flashed 1k polling rate for the ctl-472.
To Do: Compare Ctl-490.

https://github.com/user-attachments/assets/3f4560cf-8e2d-4353-a79d-ccce73de0815

---

### Math used to save and log later.

---

### 1. Extract XY coordinates

**SVG:** Parse vector paths into coordinate points <i>(x<sub>i</sub>, y<sub>i</sub>)</i>. Curves may need to be sampled into smaller line segments.

**OSR:** Parse recorded cursor positions and the time difference between replay frames.

<div align="center">
  <p><i>P<sub>i</sub> = (x<sub>i</sub>, y<sub>i</sub>, t<sub>i</sub>)</i></p>
</div>

---

### 2. Scale coordinates to the machine

Convert the original coordinate system to physical millimeters.

<div align="center">
  <p><i>X<sub>i</sub> = x<sub>i</sub> &times; (W<sub>machine</sub> / W<sub>source</sub>)</i></p>
  <p><i>Y<sub>i</sub> = y<sub>i</sub> &times; (H<sub>machine</sub> / H<sub>source</sub>)</i></p>
</div>

For osu!, the standard playfield is 512 &times; 384.

---

### 3. Calculate travel distance

Use the Euclidean distance between consecutive points:

<div align="center">
  <p><kbd><i>d<sub>i</sub> = &radic;((X<sub>i+1</sub> &minus; X<sub>i</sub>)<sup>2</sup> + (Y<sub>i+1</sub> &minus; Y<sub>i</sub>)<sup>2</sup>)</i></kbd></p>
</div>

This determines how far the machine must travel.

---

### 4. Calculate motion speed and time

For an OSR replay, the timestamps specify the available movement time:

<div align="center">
  <p><i>&Delta;t<sub>i</sub> = (t<sub>i+1</sub> &minus; t<sub>i</sub>) / 1000</i></p>
</div>

Assuming timestamps are measured in milliseconds, the required average velocity is:

<div align="center">
  <p><kbd><i>v<sub>i</sub> = d<sub>i</sub> / &Delta;t<sub>i</sub></i></kbd></p>
</div>

Compare this with the maximum machine velocity:

<div align="center">
  <p><i>v<sub>i</sub> &le; v<sub>max</sub></i></p>
</div>

An SVG normally does not have replay timestamps, so movement timing instead depends on the chosen speed and motion constraints.

For a simple constant-speed calculation:

<div align="center">
  <p><i>t<sub>i</sub> = d<sub>i</sub> / v<sub>i</sub></i></p>
</div>

---

### 5. Account for acceleration

Assuming each move starts and ends at rest, maximum acceleration is <i>a<sub>max</sub></i>:

<div align="center">
  <table>
    <tr>
      <td rowspan="2"><i>t<sub>min</sub></i> = </td>
      <td>2&radic;(<i>d</i> / <i>a<sub>max</sub></i>),</td>
      <td><i>d</i> &le; <i>v<sub>max</sub></i><sup>2</sup> / <i>a<sub>max</sub></i></td>
    </tr>
    <tr>
      <td>(<i>d</i> / <i>v<sub>max</sub></i>) + (<i>v<sub>max</sub></i> / <i>a<sub>max</sub></i>),</td>
      <td><i>d</i> &gt; <i>v<sub>max</sub></i><sup>2</sup> / <i>a<sub>max</sub></i></td>
    </tr>
  </table>
</div>

---
