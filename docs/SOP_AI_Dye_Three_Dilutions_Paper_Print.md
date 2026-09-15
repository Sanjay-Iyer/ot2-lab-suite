# SOP 1 — Make Dilutions and Print Column 1

**Goal:** Dilute dye with water to make 2x, 5x, and 10x dilutions. Make each
dilution to 100 µL total volume, then print one 5 µL drop of each dilution in
paper column 1.

1. Run the script:

   ```powershell
   conda activate llm
   python scripts\ai_dye_demo.py
   ```

2. Wait for `connection established`, then type your name when asked
   **Who is running this experiment?**

3. Tell the robot to set up the dilution and printing plan:

   - Make three dye dilutions with water: 2x, 5x, and 10x.
   - Make each dilution to 100 µL total volume.
   - Use plate column 11, starting at row A.
   - Put the paper in deck slot 5.
   - Print one 5 µL drop from each dilution in paper column 1.
   - Use one replicate and start from tip A1.

4. Read the **PROPOSED PLAN** (the complete plan after yes) and its **ATTENTION** block,
   including any **CHECK THESE** lines. If it matches your request, type `yes`. If not, type
   `no` and say it again.

5. Type `plan`. Confirm the settings and the physical deck match, and that the tips are
   A1-E1. If unsure, refer to the [Guide](GUIDE_AI_Dye_Three_Dilutions_Paper_Print.md).

6. When the robot is physically ready, type `run`.

7. After the run, the agent proposes starting tip **F1** for the next run. Type `yes`.
   Stay in the session for SOP 2, or type `quit`.

# SOP 2 — Print Multiple Drops in Column 2

**Goal:** Use the three existing 2x, 5x, and 10x dilutions and print three 5 µL
drops of each dilution in paper column 2.

1. Continue in the same session. If you quit, run `python scripts\ai_dye_demo.py`
   again and type your name.

2. Tell the robot to set up the printing plan:

   - The three dilutions are already made; skip dilution-making.
   - Use the existing 2x, 5x, and 10x dilutions in plate column 11, starting at row A.
     Each well now holds about 95 µL.
   - Keep the paper in deck slot 5.
   - Print three separate 5 µL drops from each dilution in paper column 2.
   - Stack the three drops on the same spot.
   - Use one replicate and start from tip F1.

3. Read the **PROPOSED PLAN**. DILUTIONS must say `SKIPPED - already in the plate`, and
   ATTENTION must say
   `Saying yes records plate wells A11-C11 as already holding the dilutions (2× | 5× | 10×; ...)`. Type `yes`.

4. Type `plan`. Confirm:
   - DILUTIONS is skipped;
   - PRINTING shows paper column 2, paper rows A | B | C and 3 drops per position (`steps` lists
     each print step FROM wells A11, B11, C11 TO paper positions A2, B2, C2);
   - PIPETTING shows tips F1-H1;
   - the physical deck matches.

   If unsure, refer to the [Guide](GUIDE_AI_Dye_Three_Dilutions_Paper_Print.md).

5. When the robot is physically ready, type `run`. When asked whether plate wells
   A11-C11 already hold the dilutions, type `yes`.
