import subprocess
import hashlib
import socket
import os
from datetime import datetime

SECRET_SALT = "Cafeto24_Super_Secret_Salt_Key_2026"

def obtener_machine_id():
    parts = []
    # 1. UUID de placa madre
    # Intentar obtener UUID usando WMIC (más rápido y menos propenso a bloqueos que PowerShell)
    try:
        out = subprocess.check_output('wmic path win32_computersystemproduct get uuid', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if len(lines) > 1:
            uuid_val = lines[1]
            if uuid_val and len(uuid_val) > 5 and "Default" not in uuid_val and "default" not in uuid_val.lower():
                parts.append(uuid_val)
    except Exception:
        pass

    # Fallback a PowerShell si WMIC falla o no retorna un UUID válido
    if not parts:
        try:
            uuid_val = subprocess.check_output('powershell -NoProfile -Command "(Get-CimInstance Win32_ComputerSystemProduct).UUID"', shell=True, text=True, timeout=3).strip()
            if uuid_val and len(uuid_val) > 5:
                parts.append(uuid_val)
        except Exception:
            pass
        
    # 2. Serial de placa madre
    # Intentar obtener serial de placa usando WMIC
    try:
        out = subprocess.check_output('wmic path win32_baseboard get serialnumber', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if len(lines) > 1:
            serial_val = lines[1]
            if serial_val and len(serial_val) > 2 and "Default" not in serial_val and "default" not in serial_val.lower():
                parts.append(serial_val)
    except Exception:
        pass

    if len(parts) < 2:  # Si falta el serial de la placa en parts
        try:
            serial_val = subprocess.check_output('powershell -NoProfile -Command "(Get-CimInstance Win32_BaseBoard).SerialNumber"', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
            if serial_val and len(serial_val) > 2 and serial_val not in parts:
                parts.append(serial_val)
        except Exception:
            pass

    # 3. CPU ID
    # Intentar obtener CPU ID usando WMIC
    try:
        out = subprocess.check_output('wmic path win32_processor get processorid', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL)
        lines = [line.strip() for line in out.splitlines() if line.strip()]
        if len(lines) > 1:
            cpu_val = lines[1]
            if cpu_val and len(cpu_val) > 2:
                parts.append(cpu_val)
    except Exception:
        pass

    if len(parts) < 3:  # Si falta CPU ID en parts
        try:
            cpu_val = subprocess.check_output('powershell -NoProfile -Command "(Get-CimInstance Win32_Processor).ProcessorId"', shell=True, text=True, timeout=3, stderr=subprocess.DEVNULL).strip()
            if cpu_val and len(cpu_val) > 2 and cpu_val not in parts:
                parts.append(cpu_val)
        except Exception:
            pass

    # 4. Fallback robusto al Registro de Windows (MachineGuid) si todo lo anterior falló
    if not parts:
        try:
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography") as key:
                guid, _ = winreg.QueryValueEx(key, "MachineGuid")
                if guid:
                    parts.append(guid.strip())
        except Exception:
            pass

    # Fallback final si falla todo lo anterior
    if not parts:
        try:
            fallback = f"{socket.gethostname()}-{os.getlogin() if hasattr(os, 'getlogin') else 'user'}"
            parts.append(fallback)
        except Exception:
            parts.append("CAFETO24-DEFAULT-MACHINE-KEY")
            
    combined = "|".join(parts)
    sha = hashlib.sha256(combined.encode('utf-8')).hexdigest().upper()
    return "-".join([sha[i:i+5] for i in range(0, 20, 5)])

def generar_clave_licencia(machine_id, fecha_expiracion):
    """
    Genera una clave de activación para un Machine ID y fecha de expiración.
    fecha_expiracion puede ser 'indefinida' o formato 'YYYY-MM-DD'.
    """
    data = f"{machine_id}|{fecha_expiracion}"
    hash_val = hashlib.sha256(f"{data}|{SECRET_SALT}".encode('utf-8')).hexdigest().upper()[:16]
    # Clave de licencia formateada amigablemente
    raw_key = f"{data}|{hash_val}"
    return raw_key.encode('utf-8').hex().upper()

def validar_licencia(licencia_hex, machine_id):
    """
    Valida una clave de licencia en formato hexadecimal.
    Retorna (es_valido, mensaje)
    """
    if not licencia_hex:
        return False, "No se ha ingresado ninguna licencia."
    try:
        licencia_str = bytes.fromhex(licencia_hex.strip()).decode('utf-8')
        parts = licencia_str.split('|')
        if len(parts) != 3:
            return False, "La clave de licencia tiene un formato incorrecto."
            
        m_id, fecha_exp, hash_val = parts
        if m_id != machine_id:
            return False, "Esta licencia corresponde a otro equipo físico."
            
        # Validar fecha de expiración
        if fecha_exp != 'indefinida':
            try:
                exp_date = datetime.strptime(fecha_exp, "%Y-%m-%d")
                if datetime.now() > exp_date:
                    return False, f"La licencia expiró el {fecha_exp}."
            except ValueError:
                return False, "Fecha de expiración de licencia corrupta."
                
        # Validar firma criptográfica
        expected_hash = hashlib.sha256(f"{m_id}|{fecha_exp}|{SECRET_SALT}".encode('utf-8')).hexdigest().upper()[:16]
        if hash_val != expected_hash:
            return False, "La clave de licencia es inválida o ha sido alterada."
            
        return True, "Licencia activa y válida."
    except Exception as e:
        return False, f"Error de decodificación de licencia: {e}"
