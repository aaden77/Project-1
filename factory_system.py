#!/usr/bin/env python3
"""
Factory Automation Management System - SmartTech Manufacturing Ltd.

A menu-driven console application that records and monitors factory
operations: machines, products, production orders, operators/technicians,
machine status, production output, maintenance activities and costs.

Usage:
    python3 factory_system.py

All records are saved to factory_data.json (next to this file) after every
operation, so data survives restarts. On first run the store is seeded with
the standard product catalogue. Requires Python 3.8+, standard library only.
"""

import datetime
import getpass
import hashlib
import hmac
import json
import math
import os
import re
import sys

# ---------------------------------------------------------------------------
# Constants (tuples hold the fixed lists of valid values)
# ---------------------------------------------------------------------------
DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "factory_data.json")
TABLES = ("machines", "products", "employees", "orders", "maintenance")
MAX_LOGIN_ATTEMPTS = 3
MAX_ORDER_QUANTITY = 1_000_000
CANCEL_KEY = "Q"
CANCEL_HINT = "  (Type Q at any prompt to cancel and return to the main menu.)"

# Username -> SHA-256 digest of the password; clear-text passwords are never stored.
# shortcut: unsalted hashes kept in source; move to salted hashlib.scrypt hashes in a protected user file before wider rollout.
AUTHORIZED_USERS = {
    "admin": "008e257f27066127e41d91e93edf907279ec1edb5702af079aba7d12d33d76c9",
    "supervisor": "73a0df17d5eae70d1e53d5e73f83657081034552a4ec80cb9d672bfc4e18c148",
}

STATUS_AVAILABLE = "AVAILABLE"
STATUS_IN_PRODUCTION = "IN PRODUCTION"
STATUS_MAINTENANCE = "MAINTENANCE"
STATUS_OUT_OF_SERVICE = "OUT OF SERVICE"
MACHINE_STATUSES = (STATUS_AVAILABLE, STATUS_IN_PRODUCTION, STATUS_MAINTENANCE, STATUS_OUT_OF_SERVICE)

JOB_IN_PROGRESS = "IN PROGRESS"
JOB_COMPLETED = "COMPLETED"

ROLES = ("Machine Operator", "Maintenance Technician", "Electrical Technician",
         "Automation Technician", "Production Supervisor")
TECHNICIAN_ROLES = ("Maintenance Technician", "Electrical Technician", "Automation Technician")
SHIFTS = ("Morning", "Evening", "Night")
MAINTENANCE_TYPES = ("Preventive", "Corrective", "Electrical", "Mechanical", "Software/Control-System")

# (name, category, production minutes per unit, material cost per unit, selling price per unit)
SEED_PRODUCTS = (
    ("LED Lighting Unit", "Lighting", 4, 1500, 2800),
    ("Smart Power Outlet", "Electrical", 6, 2200, 4000),
    ("Electronic Control Module", "Electronics", 10, 3500, 6500),
    ("Sensor Module", "Electronics", 5, 1800, 3200),
)

NAME_PATTERN = r"[^\W\d_]+(?:[ '\-][^\W\d_]+)*"   # letters (any language), single separators
NAME_HINT = "Use letters only, with single spaces, hyphens or apostrophes between parts."
PHONE_PATTERN = r"\+?\d[\d \-]{5,18}\d"
PHONE_HINT = "Use 7-20 characters: digits, spaces or hyphens, with an optional leading +."


class OperationCancelled(Exception):
    """Raised when the user types the cancel key, abandoning the current operation."""


# ---------------------------------------------------------------------------
# Input helpers - every prompt repeats until the entry is valid
# ---------------------------------------------------------------------------
def ask(message):
    """Return one trimmed line of user input; raise OperationCancelled on 'Q'."""
    value = input(message).strip()
    if value.upper() == CANCEL_KEY:
        raise OperationCancelled
    return value


def prompt_text(message, default=None, pattern=None, hint="", max_length=60):
    """Prompt until a non-empty value of at most max_length characters is entered.

    If pattern is given the value must fully match it. When default is not
    None, pressing Enter keeps the default (used when updating records).
    """
    suffix = f" [{default}]" if default is not None else ""
    while True:
        value = ask(f"  {message}{suffix}: ")
        if not value and default is not None:
            return default
        if not value:
            print("  Error: this field is required and cannot be empty.")
        elif len(value) > max_length:
            print(f"  Error: please enter no more than {max_length} characters.")
        elif pattern and not re.fullmatch(pattern, value):
            print(f"  Error: invalid format. {hint}")
        else:
            return value


def prompt_number(message, cast=float, allow_zero=True, maximum=None, default=None):
    """Prompt until a valid, non-negative number is entered.

    cast is int for whole numbers or float for amounts. Rejects empty input,
    text, negatives, NaN/infinity, zero (when allow_zero is False) and values
    above maximum. Pressing Enter keeps default when one is supplied.
    """
    suffix = f" [{default}]" if default is not None else ""
    kind = "whole number" if cast is int else "number"
    while True:
        raw = ask(f"  {message}{suffix}: ")
        if not raw and default is not None:
            return default
        try:
            value = cast(raw)
        except ValueError:
            value = None
        if not raw:
            print("  Error: this field is required and cannot be empty.")
        elif value is None or not math.isfinite(value):
            print(f"  Error: '{raw}' is not a valid {kind}.")
        elif value < 0:
            print("  Error: value cannot be negative.")
        elif value == 0 and not allow_zero:
            print("  Error: value must be greater than zero.")
        elif maximum is not None and value > maximum:
            print(f"  Error: value cannot be more than {maximum:,}.")
        else:
            return value


def prompt_choice(message, options, default=None):
    """Display numbered options and return the chosen one.

    Accepts the option number or the option text in any letter case.
    """
    print(f"  {message}:")
    for number, option in enumerate(options, start=1):
        print(f"    {number}. {option}")
    by_text = {option.upper(): option for option in options}
    suffix = f" [{default}]" if default is not None else ""
    while True:
        raw = ask(f"  Enter choice (1-{len(options)}){suffix}: ")
        if not raw and default is not None:
            return default
        if raw.isdecimal() and 1 <= int(raw) <= len(options):
            return options[int(raw) - 1]
        if raw.upper() in by_text:
            return by_text[raw.upper()]
        print(f"  Error: '{raw}' is not a valid option. Enter a number from 1 to {len(options)}.")


def prompt_yes_no(message):
    """Prompt until Y/YES or N/NO is entered; return True for yes."""
    while True:
        answer = ask(f"  {message} (Y/N): ").upper()
        if answer in ("Y", "YES"):
            return True
        if answer in ("N", "NO"):
            return False
        print("  Error: please enter Y or N.")


def prompt_id(message, table, label, check=None):
    """Prompt until the ID of an existing record in table is entered.

    check(record_id) may return an error message to reject a record that
    exists but is not eligible (for example a machine under maintenance).
    """
    while True:
        record_id = ask(f"  {message}: ").upper()
        if not record_id:
            problem = "an ID is required."
        elif record_id not in table:
            problem = f"'{record_id}' is not a valid {label} ID. Use an ID from the list above."
        else:
            problem = check(record_id) if check else None
        if not problem:
            return record_id
        print(f"  Error: {problem}")


# ---------------------------------------------------------------------------
# Small calculation and formatting helpers
# ---------------------------------------------------------------------------
def next_id(prefix, table, width=3):
    """Return the next sequential ID for table, e.g. M001 or PO0001.

    Records are never deleted, so count + 1 is always unused.
    """
    return f"{prefix}{len(table) + 1:0{width}d}"


def money(amount):
    """Format an amount as currency, e.g. $1,500.00."""
    return f"${amount:,.2f}"


def full_name(employee):
    """Return an employee's first and last name."""
    return f"{employee['first_name']} {employee['last_name']}"


def defect_rate(good, defective):
    """Return defective units as a percentage of total production (0 if nothing was produced)."""
    total = good + defective
    return defective / total * 100 if total else 0.0


def print_header(title):
    """Print a boxed section title."""
    line = "=" * 72
    print(f"\n{line}\n{title.center(72)}\n{line}")


def print_details(pairs):
    """Print (label, value) pairs as an aligned block."""
    for label, value in pairs:
        print(f"  {label:<26}: {value}")


def print_table(headers, rows):
    """Print rows as an aligned text table, or a notice when there are none."""
    if not rows:
        print("  No records found.")
        return
    rows = [[str(cell) for cell in row] for row in rows]
    widths = [max(len(header), *(len(row[i]) for row in rows)) for i, header in enumerate(headers)]
    for row in (headers, ["-" * width for width in widths], *rows):
        print("  " + "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip())


# ---------------------------------------------------------------------------
# Data storage
# ---------------------------------------------------------------------------
def load_data(path=DATA_FILE):
    """Return saved records from path, or a new store seeded with the standard products.

    Raises OSError/ValueError if the file exists but cannot be read as a data file.
    """
    if not os.path.exists(path):
        data = {table: {} for table in TABLES}
        for name, category, minutes, material_cost, price in SEED_PRODUCTS:
            data["products"][next_id("P", data["products"])] = {
                "name": name, "category": category, "time_per_unit": minutes,
                "material_cost": material_cost, "selling_price": price,
            }
        return data
    with open(path, encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict) or not set(TABLES) <= data.keys():
        raise ValueError("unexpected file structure")
    return data


def save_data(data, path=DATA_FILE):
    """Write all records atomically, so a crash mid-write cannot corrupt the file."""
    temp_path = path + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=2)
    os.replace(temp_path, path)


# ---------------------------------------------------------------------------
# Login and main menu
# ---------------------------------------------------------------------------
def login():
    """Allow up to MAX_LOGIN_ATTEMPTS tries; return True on success, False otherwise."""
    print_header("SMARTTECH MANUFACTURING LTD. - AUTHORIZED PERSONNEL ONLY")
    read_password = getpass.getpass if sys.stdin.isatty() else input  # input() lets scripted sessions log in
    for attempt in range(1, MAX_LOGIN_ATTEMPTS + 1):
        username = input("Username: ").strip().lower()
        password = read_password("Password: ")
        digest = hashlib.sha256(password.encode()).hexdigest()
        if hmac.compare_digest(digest, AUTHORIZED_USERS.get(username, "")):
            print(f"\nLogin successful. Welcome, {username}.")
            return True
        remaining = MAX_LOGIN_ATTEMPTS - attempt
        if remaining:
            print(f"Invalid username or password. {remaining} attempt(s) remaining.\n")
    print("\nMaximum login attempts exceeded. Access denied - the program will now terminate.")
    return False


def display_main_menu():
    """Print the main menu options."""
    print_header("FACTORY AUTOMATION MANAGEMENT SYSTEM")
    for number, (label, _) in MAIN_MENU.items():
        print(f"  {number}. {label}")


def machine_busy_reason(data, machine_id):
    """Return why a machine is locked by an in-progress order or maintenance job, else None."""
    for table, label, option in (("orders", "production order", 1), ("maintenance", "maintenance job", 6)):
        for job_id, job in data[table].items():
            if job["machine_id"] == machine_id and job["status"] == JOB_IN_PROGRESS:
                return (f"machine {machine_id} has in-progress {label} {job_id}. "
                        f"Complete it first (main menu option {option}).")
    return None


# ---------------------------------------------------------------------------
# 1. Start Production Order
# ---------------------------------------------------------------------------
def start_production_order(data):
    """Menu 1: start a production order on an AVAILABLE machine, or complete an open order."""
    print_header("START PRODUCTION ORDER")
    print(CANCEL_HINT)
    if any(order["status"] == JOB_IN_PROGRESS for order in data["orders"].values()):
        action = prompt_choice("Select action", ("Start a new production order", "Complete an in-progress order"))
        if action != "Start a new production order":
            complete_production_order(data)
            return
    machines = data["machines"]
    has_available = any(m["status"] == STATUS_AVAILABLE for m in machines.values())
    missing = [item for item, present in (("a product", data["products"]),
                                           ("an AVAILABLE machine", has_available),
                                           ("an operator/technician", data["employees"])) if not present]
    if missing:
        print(f"  Cannot start production. The system needs {', '.join(missing)} first.")
        return

    product_report(data)
    product_id = prompt_id("Enter Product ID", data["products"], "product")
    machine_report(data)
    machine_id = prompt_id("Enter Machine ID", machines, "machine",
                           check=lambda mid: None if machines[mid]["status"] == STATUS_AVAILABLE
                           else f"machine {mid} is {machines[mid]['status']} and cannot be assigned "
                                f"to production. Choose an AVAILABLE machine.")
    employee_report(data)
    employee_id = prompt_id("Enter Operator/Technician ID", data["employees"], "employee")
    quantity = prompt_number("Quantity required (units)", cast=int, allow_zero=False, maximum=MAX_ORDER_QUANTITY)

    product, machine, employee = data["products"][product_id], machines[machine_id], data["employees"][employee_id]
    hours = quantity * product["time_per_unit"] / 60
    material_cost = quantity * product["material_cost"]
    machine_cost = hours * machine["cost_per_hour"]
    order_id = next_id("PO", data["orders"], width=4)
    order = {
        "date": datetime.date.today().isoformat(),
        "product_id": product_id, "product_name": product["name"],
        "machine_id": machine_id, "machine_name": machine["name"],
        "employee_id": employee_id, "employee_name": full_name(employee),
        "quantity": quantity, "estimated_hours": hours,
        "estimated_cost": round(material_cost + machine_cost, 2),
        "good": None, "defective": None, "status": JOB_IN_PROGRESS, "completed_date": None,
    }
    print_header(f"PRODUCTION ORDER {order_id}")
    print_details((
        ("Date", order["date"]),
        ("Product", f"{product_id} - {product['name']}"),
        ("Machine", f"{machine_id} - {machine['name']} ({machine['type']})"),
        ("Operator/Technician", f"{employee_id} - {order['employee_name']} ({employee['role']})"),
        ("Quantity required", f"{quantity:,} units"),
        ("Estimated machine time", f"{hours:,.2f} hours"),
        ("Material cost", f"{money(material_cost)} ({quantity:,} x {money(product['material_cost'])})"),
        ("Machine operating cost", f"{money(machine_cost)} ({hours:,.2f} h x {money(machine['cost_per_hour'])}/h)"),
        ("Estimated production cost", money(order["estimated_cost"])),
    ))
    if not prompt_yes_no("Start this production order?"):
        print("  Production order not started. No changes were made.")
        return
    data["orders"][order_id] = order
    machine["status"] = STATUS_IN_PRODUCTION
    print(f"  Order {order_id} started. Machine {machine_id} is now {STATUS_IN_PRODUCTION}.")
    if prompt_yes_no("Is the production run finished (record output now)?"):
        complete_production_order(data, order_id)
    else:
        print("  Record the output later with option 1 > Complete an in-progress order.")


def complete_production_order(data, order_id=None):
    """Record good and defective output for an open order, then release its machine."""
    orders = data["orders"]
    if order_id is None:
        print_table(("Order ID", "Date", "Product", "Machine", "Operator", "Qty Req."),
                    [(oid, o["date"], o["product_name"], o["machine_name"], o["employee_name"], o["quantity"])
                     for oid, o in orders.items() if o["status"] == JOB_IN_PROGRESS])
        order_id = prompt_id("Enter Production Order ID to complete", orders, "production order",
                             check=lambda oid: None if orders[oid]["status"] == JOB_IN_PROGRESS
                             else f"order {oid} is already completed.")
    order = orders[order_id]
    quantity = order["quantity"]
    good = prompt_number(f"Acceptable (good) units produced (0-{quantity:,})", cast=int, maximum=quantity)
    defective = prompt_number(f"Defective units produced (0-{quantity - good:,})", cast=int,
                              maximum=quantity - good)
    total = good + defective
    run_hours = order["estimated_hours"] * total / quantity
    machine = data["machines"][order["machine_id"]]
    machine["operating_hours"] = round(machine["operating_hours"] + run_hours, 2)
    machine["status"] = STATUS_AVAILABLE
    order.update(good=good, defective=defective, status=JOB_COMPLETED,
                 completed_date=datetime.date.today().isoformat())
    print(f"\n  Production order {order_id} completed.")
    print_details((
        ("Product", order["product_name"]),
        ("Good units", f"{good:,}"),
        ("Defective units", f"{defective:,}"),
        ("Total production", f"{total:,}"),
        ("Defect rate", f"{defect_rate(good, defective):.2f}%"),
        ("Machine hours added", f"{run_hours:,.2f} (machine {order['machine_id']} is now AVAILABLE)"),
    ))


# ---------------------------------------------------------------------------
# 2. Add Machine(s)
# ---------------------------------------------------------------------------
def add_machines(data):
    """Menu 2: add machines one after another until the user answers N."""
    print_header("ADD MACHINE(S)")
    print(CANCEL_HINT)
    while True:
        machine_id = next_id("M", data["machines"])
        print(f"\n  New Machine ID: {machine_id}")
        machine = {
            "name": prompt_text("Machine name"),
            "type": prompt_text("Machine type (e.g. SMT Placement, Soldering, Conveyor)"),
            "capacity": prompt_number("Production capacity (units/hour)", cast=int, allow_zero=False),
            "cost_per_hour": prompt_number("Operating cost per hour ($)"),
            "status": prompt_choice("Initial status",
                                    (STATUS_AVAILABLE, STATUS_MAINTENANCE, STATUS_OUT_OF_SERVICE),
                                    default=STATUS_AVAILABLE),
            "operating_hours": prompt_number("Total operating hours to date", default=0.0),
        }
        data["machines"][machine_id] = machine
        print(f"  Machine {machine_id} '{machine['name']}' added with status {machine['status']}.")
        if not prompt_yes_no("Add another machine?"):
            break


# ---------------------------------------------------------------------------
# 3. Add/Update Product
# ---------------------------------------------------------------------------
def add_update_product(data):
    """Menu 3: add a new product or update an existing product's details."""
    print_header("ADD / UPDATE PRODUCT")
    print(CANCEL_HINT)
    product_report(data)
    action = prompt_choice("Select action", ("Add a new product", "Update an existing product"))
    if action == "Add a new product":
        product_id, current = next_id("P", data["products"]), None
        print(f"\n  New Product ID: {product_id}")
    else:
        product_id = prompt_id("Enter Product ID to update", data["products"], "product")
        current = data["products"][product_id]
        print("  Press Enter to keep the current value shown in [brackets].")
    data["products"][product_id] = product_form(data, product_id, current)
    print(f"  Product {product_id} {'added' if current is None else 'updated'}.")


def product_form(data, product_id, current=None):
    """Collect validated product fields; values in current are the defaults when updating."""
    current = current or {}
    taken_names = {p["name"].upper() for pid, p in data["products"].items() if pid != product_id}
    while True:
        name = prompt_text("Product name", default=current.get("name"))
        if name.upper() not in taken_names:
            break
        print(f"  Error: a product named '{name}' already exists.")
    return {
        "name": name,
        "category": prompt_text("Category", default=current.get("category")),
        "time_per_unit": prompt_number("Production time per unit (minutes)", allow_zero=False,
                                       default=current.get("time_per_unit")),
        "material_cost": prompt_number("Material cost per unit ($)", default=current.get("material_cost")),
        "selling_price": prompt_number("Selling price per unit ($)", default=current.get("selling_price")),
    }


# ---------------------------------------------------------------------------
# 4. Add Operator/Technician
# ---------------------------------------------------------------------------
def add_employee(data):
    """Menu 4: register an operator/technician with validated details."""
    print_header("ADD OPERATOR / TECHNICIAN")
    print(CANCEL_HINT)
    employee_id = next_id("E", data["employees"])
    print(f"\n  New Employee ID: {employee_id}")
    employee = {
        "first_name": prompt_text("First name", pattern=NAME_PATTERN, hint=NAME_HINT),
        "last_name": prompt_text("Last name", pattern=NAME_PATTERN, hint=NAME_HINT),
        "contact": prompt_text("Contact number", pattern=PHONE_PATTERN, hint=PHONE_HINT),
        "role": prompt_choice("Role", ROLES),
        "specialization": prompt_text("Machine specialization (e.g. SMT Placement, Conveyor)"),
        "shift": prompt_choice("Shift", SHIFTS),
    }
    data["employees"][employee_id] = employee
    print(f"  Employee {employee_id} {full_name(employee)} added as {employee['role']} "
          f"({employee['shift']} shift).")


# ---------------------------------------------------------------------------
# 5. Update Machine Status
# ---------------------------------------------------------------------------
def update_machine_status(data):
    """Menu 5: find a machine by ID and set a new valid status."""
    print_header("UPDATE MACHINE STATUS")
    if not data["machines"]:
        print("  No machines recorded yet. Add machines with option 2 first.")
        return
    print(CANCEL_HINT)
    machine_report(data)
    machine_id = prompt_id("Enter Machine ID", data["machines"], "machine",
                           check=lambda mid: machine_busy_reason(data, mid))
    machine = data["machines"][machine_id]
    print(f"  Current status of {machine_id} ({machine['name']}): {machine['status']}")
    new_status = prompt_choice("New status", MACHINE_STATUSES)
    if new_status == machine["status"]:
        print(f"  Machine {machine_id} is already {new_status}. No change made.")
        return
    old_status, machine["status"] = machine["status"], new_status
    print(f"  Machine {machine_id} status changed from {old_status} to {new_status}.")
    if new_status in (STATUS_MAINTENANCE, STATUS_OUT_OF_SERVICE):
        print("  This machine cannot be assigned to production until it is AVAILABLE again.")


# ---------------------------------------------------------------------------
# 6. Record Maintenance
# ---------------------------------------------------------------------------
def record_maintenance(data):
    """Menu 6: start a maintenance job (machine -> MAINTENANCE) or complete an open one."""
    print_header("RECORD MAINTENANCE")
    print(CANCEL_HINT)
    if any(job["status"] == JOB_IN_PROGRESS for job in data["maintenance"].values()):
        action = prompt_choice("Select action", ("Start a new maintenance job",
                                                 "Complete an in-progress maintenance job"))
        if action != "Start a new maintenance job":
            complete_maintenance(data)
            return
    employees = data["employees"]
    has_technician = any(e["role"] in TECHNICIAN_ROLES for e in employees.values())
    missing = [item for item, present in (("a machine", data["machines"]),
                                           ("a technician", has_technician)) if not present]
    if missing:
        print(f"  Cannot record maintenance. The system needs {' and '.join(missing)} first.")
        return

    machine_report(data)
    machine_id = prompt_id("Enter Machine ID", data["machines"], "machine",
                           check=lambda mid: machine_busy_reason(data, mid))
    employee_report(data)
    technician_id = prompt_id("Enter Technician ID", employees, "employee",
                              check=lambda eid: None if employees[eid]["role"] in TECHNICIAN_ROLES
                              else f"{eid} is a {employees[eid]['role']}. Maintenance must be done by a "
                                   f"Maintenance, Electrical or Automation Technician.")
    maintenance_type = prompt_choice("Maintenance type", MAINTENANCE_TYPES)
    description = prompt_text("Problem description", max_length=200)
    cost = prompt_number("Maintenance cost ($)")

    machine = data["machines"][machine_id]
    maintenance_id = next_id("MT", data["maintenance"], width=4)
    job = {
        "date": datetime.date.today().isoformat(),
        "machine_id": machine_id, "machine_name": machine["name"],
        "technician_id": technician_id, "technician_name": full_name(employees[technician_id]),
        "type": maintenance_type, "description": description, "cost": cost,
        "operating_hours": machine["operating_hours"],
        "status": JOB_IN_PROGRESS, "completed_date": None,
    }
    print_header(f"MAINTENANCE RECORD {maintenance_id}")
    print_details((
        ("Date", job["date"]),
        ("Machine", f"{machine_id} - {machine['name']} (currently {machine['status']})"),
        ("Technician", f"{technician_id} - {job['technician_name']}"),
        ("Maintenance type", maintenance_type),
        ("Problem description", description),
        ("Maintenance cost", money(cost)),
        ("Machine operating hours", f"{machine['operating_hours']:,.2f}"),
    ))
    if not prompt_yes_no("Start this maintenance job?"):
        print("  Maintenance not recorded. No changes were made.")
        return
    data["maintenance"][maintenance_id] = job
    machine["status"] = STATUS_MAINTENANCE
    print(f"  Maintenance {maintenance_id} started. Machine {machine_id} is now {STATUS_MAINTENANCE}.")
    if prompt_yes_no("Is the maintenance work finished?"):
        complete_maintenance(data, maintenance_id)
    else:
        print("  Complete it later with option 6 > Complete an in-progress maintenance job.")


def complete_maintenance(data, maintenance_id=None):
    """Mark an open maintenance job completed and optionally return its machine to AVAILABLE."""
    jobs = data["maintenance"]
    if maintenance_id is None:
        print_table(("Maint. ID", "Date", "Machine", "Technician", "Type"),
                    [(jid, j["date"], f"{j['machine_id']} {j['machine_name']}", j["technician_name"], j["type"])
                     for jid, j in jobs.items() if j["status"] == JOB_IN_PROGRESS])
        maintenance_id = prompt_id("Enter Maintenance ID to complete", jobs, "maintenance",
                                   check=lambda jid: None if jobs[jid]["status"] == JOB_IN_PROGRESS
                                   else f"maintenance {jid} is already completed.")
    job = jobs[maintenance_id]
    machine = data["machines"][job["machine_id"]]
    make_available = prompt_yes_no(f"Return machine {job['machine_id']} to AVAILABLE?")
    job.update(status=JOB_COMPLETED, completed_date=datetime.date.today().isoformat())
    if make_available:
        machine["status"] = STATUS_AVAILABLE
    print(f"  Maintenance {maintenance_id} completed. Machine {job['machine_id']} is {machine['status']}.")
    if not make_available:
        print("  Use option 5 to change the machine's status when it is ready.")


# ---------------------------------------------------------------------------
# 7. Print Report
# ---------------------------------------------------------------------------
def print_report(data):
    """Menu 7: print any of the six reports until the user enters 0."""
    while True:
        print_header("PRINT REPORT")
        for number, (title, _) in enumerate(REPORTS, start=1):
            print(f"  {number}. {title}")
        print("  0. Return to main menu")
        choice = input(f"Enter report choice (0-{len(REPORTS)}): ").strip()
        if choice == "0":
            return
        if choice.isdecimal() and 1 <= int(choice) <= len(REPORTS):
            title, report = REPORTS[int(choice) - 1]
            print_header(title.upper())
            report(data)
        else:
            print(f"  Error: '{choice}' is not a valid report choice. Enter a number from 0 to {len(REPORTS)}.")


def machine_report(data):
    """Machine Report: every machine's details plus the total number of machines."""
    print_table(("Machine ID", "Name", "Type", "Capacity", "Cost/Hour", "Op. Hours", "Status"),
                [(mid, m["name"], m["type"], f"{m['capacity']:,} u/h", money(m["cost_per_hour"]),
                  f"{m['operating_hours']:,.2f}", m["status"]) for mid, m in data["machines"].items()])
    print(f"\n  Total number of machines: {len(data['machines'])}")


def production_report(data):
    """Production Report: each order's output, total production, defect rate and estimated cost."""
    rows = []
    total_good = total_defective = 0
    for order_id, o in data["orders"].items():
        if o["status"] == JOB_COMPLETED:
            good, defective = o["good"], o["defective"]
            total_good += good
            total_defective += defective
            output = (good, defective, good + defective, f"{defect_rate(good, defective):.2f}%")
        else:
            output = ("-", "-", "-", "-")
        rows.append((order_id, o["product_name"], o["machine_name"], o["employee_name"], o["quantity"],
                     *output, money(o["estimated_cost"]), o["status"]))
    print_table(("Order ID", "Product", "Machine", "Operator", "Qty Req", "Good", "Defect",
                 "Total", "Defect %", "Est. Cost", "Status"), rows)
    completed = sum(o["status"] == JOB_COMPLETED for o in data["orders"].values())
    print()
    print_details((
        ("Production orders", f"{len(rows)} ({completed} completed, {len(rows) - completed} in progress)"),
        ("Total good units", f"{total_good:,}"),
        ("Total defective units", f"{total_defective:,}"),
        ("Total production", f"{total_good + total_defective:,}"),
        ("Overall defect rate", f"{defect_rate(total_good, total_defective):.2f}%"),
        ("Total estimated cost", money(sum(o["estimated_cost"] for o in data["orders"].values()))),
    ))


def product_report(data):
    """Product Report: product details with Estimated Profit = Selling Price - Material Cost."""
    print_table(("Product ID", "Name", "Category", "Time/Unit", "Material Cost", "Selling Price", "Est. Profit"),
                [(pid, p["name"], p["category"], f"{p['time_per_unit']:g} min", money(p["material_cost"]),
                  money(p["selling_price"]), money(p["selling_price"] - p["material_cost"]))
                 for pid, p in data["products"].items()])
    print(f"\n  Total number of products: {len(data['products'])}")


def machine_status_report(data):
    """Machine Status Report: machines grouped by status with a total for each group."""
    for status in MACHINE_STATUSES:
        group = [(mid, m["name"], m["type"], f"{m['operating_hours']:,.2f}")
                 for mid, m in data["machines"].items() if m["status"] == status]
        print(f"\n  {status}")
        print_table(("Machine ID", "Name", "Type", "Op. Hours"), group)
        print(f"  Total {status.lower()} machines: {len(group)}")
    print(f"\n  Total machines (all statuses): {len(data['machines'])}")


def maintenance_report(data):
    """Maintenance Report: every maintenance job plus the total maintenance cost."""
    jobs = data["maintenance"]
    print_table(("Maint. ID", "Machine", "Technician", "Type", "Date", "Cost", "Status"),
                [(jid, f"{j['machine_id']} {j['machine_name']}", j["technician_name"], j["type"], j["date"],
                  money(j["cost"]), j["status"]) for jid, j in jobs.items()])
    print(f"\n  Total maintenance records: {len(jobs)}")
    print(f"  Total maintenance cost: {money(sum(j['cost'] for j in jobs.values()))}")


def employee_report(data):
    """Employee Report: every employee plus the number assigned to each shift."""
    employees = data["employees"]
    print_table(("Employee ID", "Name", "Role", "Specialization", "Shift"),
                [(eid, full_name(e), e["role"], e["specialization"], e["shift"]) for eid, e in employees.items()])
    shift_counts = {shift: 0 for shift in SHIFTS}
    for employee in employees.values():
        shift_counts[employee["shift"]] += 1
    print("\n  Employees per shift:")
    for shift, count in shift_counts.items():
        print(f"    {shift:<8}: {count}")
    print(f"  Total employees: {len(employees)}")


# ---------------------------------------------------------------------------
# Menu tables (dictionary / tuple dispatch) and program entry point
# ---------------------------------------------------------------------------
MAIN_MENU = {
    "1": ("Start Production Order", start_production_order),
    "2": ("Add Machine(s)", add_machines),
    "3": ("Add/Update Product", add_update_product),
    "4": ("Add Operator/Technician", add_employee),
    "5": ("Update Machine Status", update_machine_status),
    "6": ("Record Maintenance", record_maintenance),
    "7": ("Print Report", print_report),
    "8": ("Exit", None),
}

REPORTS = (
    ("Machine Report", machine_report),
    ("Production Report", production_report),
    ("Product Report", product_report),
    ("Machine Status Report", machine_status_report),
    ("Maintenance Report", maintenance_report),
    ("Employee Report", employee_report),
)


def main():
    """Log in, load data, then run the main menu until Exit is chosen."""
    if not login():
        sys.exit(1)
    try:
        data = load_data()
    except (OSError, ValueError) as error:
        print(f"Error: cannot read data file {DATA_FILE}: {error}")
        print("Repair or move the file, then restart. The file has not been changed.")
        sys.exit(1)
    while True:
        display_main_menu()
        choice = input("Enter your choice (1-8): ").strip()
        if choice == "8":
            break
        if choice not in MAIN_MENU:
            print(f"  Error: '{choice}' is not a valid menu choice. Please enter a number from 1 to 8.")
            continue
        try:
            MAIN_MENU[choice][1](data)
        except OperationCancelled:
            print("\n  Operation cancelled. Returning to the main menu.")
        finally:
            try:
                save_data(data)
            except OSError as error:
                print(f"  Warning: changes could not be saved to {DATA_FILE}: {error}")
    print("\nExiting Factory Automation Management System. Goodbye.")


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, EOFError):
        print("\n\nSession ended by user.")
