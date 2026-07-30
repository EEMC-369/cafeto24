import sys, re
sys.stdout.reconfigure(encoding='utf-8')

with open('templates/ventas.html', encoding='utf-8') as f:
    lines = f.readlines()

# Find both 'dinero' function definitions
print("=== FUNCIONES 'dinero' ===")
for i, line in enumerate(lines, 1):
    if 'function dinero' in line:
        print(f"Linea {i}: {line.rstrip()}")

# Find the extra closing brace by tracking depth
print("\n=== RASTREANDO LLAVES EN SCRIPT ===")
in_script = False
depth = 0
for i, line in enumerate(lines, 1):
    if '<script>' in line and not 'type=' in line:
        in_script = True
    if '</script>' in line and in_script:
        in_script = False
    if in_script:
        for ch in line:
            if ch == '{':
                depth += 1
            elif ch == '}':
                depth -= 1
                if depth < 0:
                    print(f"LLAVE EXTRA en linea {i}: {line.rstrip()}")
                    depth = 0  # reset to continue looking
