# Offline Robot Execution

This guide covers the process of moving your protocol from a local simulation to a physical Opentrons OT-2 robot that is connected via **Ethernet** with **no internet access**.

## Why this workflow?
Standard Opentrons protocols often rely on external libraries (like `PyYAML`). On an offline robot, you cannot easily install new packages. This workflow uses **native JSON** to pass data, ensuring everything works perfectly without internet.

---

## Step 1: Physical Setup
1. **Connection**: Connect your computer directly to the Opentrons robot using an Ethernet cable.
2. **IP Address**: The robot will usually assign itself a link-local IP (e.g., `169.254.x.x`). You can find the exact IP in the Opentrons App under **Devices**.

---

## Step 2: Prepare the "Run Bundle"
Use the deployment script to convert your YAML configuration into a JSON format that the robot can read.

```bash
python tools/deploy_to_robot.py configs/my_experiment.yaml
```

**What this does:**
- Validates your YAML file.
- **Pre-deployment Simulation**: Automatically runs a local simulation of the protocol.
- **Calibration Awareness**: If you provide a `local_data_path` in your config (pointing to your robot's `/data` folder), the simulation will use your robot's real physical offsets.
- Saves a copy as `protocols/config.json`.
- Prints the exact commands you need for the next steps.

---

## Step 2.5: Configure Robot Metadata
Update your YAML configuration to include the robot's IP and local calibration data path:

```yaml
robot:
  ip_address: "169.254.x.x"
  local_data_path: "/home/sanjay/opentrons_home/OT-2_instrument/OT-2/data"
```
*Tip: Linking the `local_data_path` allows the laptop to catch "Out of Bounds" errors that would only happen on that specific physical robot.*

---

## Step 3: Transfer Files to the Robot (SCP)
Use `scp` (Secure Copy) to move the protocol and the config file onto the robot's internal storage.

```bash
scp protocols/dilution_protocol.py protocols/config.json root@<robot_ip>:/var/lib/jupyter/notebooks/
```
*Note: The password for `root` is usually blank or can be found in your Opentrons documentation.*

---

## Step 4: Execute the Protocol (SSH)
Connect to the robot's command line and start the execution.

1. **SSH into the robot**:
   ```bash
   ssh root@<robot_ip>
   ```

2. **Navigate to the notebooks directory**:
   ```bash
   cd /var/lib/jupyter/notebooks/
   ```

3. **Run the protocol**:
   ```bash
   opentrons_execute dilution_protocol.py
   ```

---

## Step 5: Handling Labware Calibration
Even when running via the command line, the robot needs to know the physical position of your plates.

1. Open the **Opentrons App** on your computer (ensure it's connected via Ethernet).
2. Upload `dilution_protocol.py` to the App.
3. Perform **Labware Calibration** as prompted.
4. **Stop there!** You don't need to run the protocol through the App. Once calibrated, the robot saves those offsets in its internal database.
5. When you run `opentrons_execute` via SSH, it will automatically look up and apply those saved offsets.

---

## Troubleshooting

### "Config file not found"
Ensure `config.json` is in the same directory as `dilution_protocol.py` on the robot (`/var/lib/jupyter/notebooks/`).

### "ModuleNotFoundError: PyYAML"
This means you are trying to run an old version of the protocol. Ensure you have transferred the updated [protocols/dilution_protocol.py](../protocols/dilution_protocol.py) which uses `json` instead of `yaml`.

### Connection Timed Out
- Check the Ethernet cable.
- Ensure your computer's Ethernet adapter is set to "DHCP" or "Link-Local".
- Ping the robot: `ping <robot_ip>`.
