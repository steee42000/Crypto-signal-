import time
import requests
import numpy as np
from datetime import datetime, timedelta
from collections import deque
import os
import pickle
import threading
import sys
import hashlib
from rich.live import Live
from rich.table import Table
from rich.console import Console
from rich.layout import Layout
from rich.panel import Panel
from rich.align import Align

# --- CONFIGURAZIONE DEMO SOLO BTC ---
BTC_CONFIG = {
    'exchange': 'binance',
    'url': 'https://api.binance.com/api/v3/ticker/24hr?symbol=BTCUSDT',
    'price_key': 'lastPrice',
    'volume_key': 'volume'
}

DATA_FILE = 'demo_btc_fedele.pkl'
LOCK_FILE = '.demo_btc_fedele.lock'
MIN_DATA_POINTS = 20  # Punti minimi per iniziare l'analisi
DEMO_DURATION_HOURS = 24  # Durata totale demo in ore

class LevelDetector:
    """Semplice detector per livelli resistenza/supporto"""
    
    def __init__(self):
        self.levels_cache = {}
    
    def detect_levels(self, prices: list, volumes: list = None) -> dict:
        """Detect livelli di resistenza e supporto"""
        if len(prices) < 10:
            return {'resistance': [], 'support': [], 'current_range': None}
        
        try:
            # Trova massimi e minimi locali
            peaks = []
            valleys = []
            
            for i in range(1, len(prices) - 1):
                # Picco (resistance)
                if prices[i] > prices[i-1] and prices[i] > prices[i+1]:
                    peaks.append(prices[i])
                # Valle (support)
                elif prices[i] < prices[i-1] and prices[i] < prices[i+1]:
                    valleys.append(prices[i])
            
            # Raggruppa livelli simili
            resistance_levels = self._cluster_levels(peaks, tolerance=0.005)
            support_levels = self._cluster_levels(valleys, tolerance=0.005)
            
            # Trova range corrente
            current_price = prices[-1]
            current_range = self._find_current_range(current_price, resistance_levels, support_levels)
            
            return {
                'resistance': resistance_levels,
                'support': support_levels,
                'current_range': current_range
            }
            
        except Exception as e:
            return {'resistance': [], 'support': [], 'current_range': None}
    
    def _cluster_levels(self, levels: list, tolerance: float) -> list:
        """Raggruppa livelli simili"""
        if not levels:
            return []
        
        levels.sort()
        clusters = []
        current_cluster = [levels[0]]
        
        for level in levels[1:]:
            if abs(level - current_cluster[-1]) <= tolerance:
                current_cluster.append(level)
            else:
                avg_level = sum(current_cluster) / len(current_cluster)
                clusters.append(avg_level)
                current_cluster = [level]
        
        if current_cluster:
            avg_level = sum(current_cluster) / len(current_cluster)
            clusters.append(avg_level)
        
        return clusters
    
    def _find_current_range(self, current_price: float, resistance_levels: list, support_levels: list) -> dict:
        """Trova il range corrente del prezzo"""
        # Trova resistenza più vicina sopra
        nearest_resistance = None
        for level in resistance_levels:
            if level > current_price:
                if nearest_resistance is None or level < nearest_resistance:
                    nearest_resistance = level
        
        # Trova supporto più vicino sotto
        nearest_support = None
        for level in support_levels:
            if level < current_price:
                if nearest_support is None or level > nearest_support:
                    nearest_support = level
        
        return {
            'resistance': nearest_resistance,
            'support': nearest_support,
            'range_size': nearest_resistance - nearest_support if nearest_resistance and nearest_support else None
        }

class DemoBTCFedeleSystem:
    def __init__(self):
        self.prices = deque(maxlen=1000)
        self.volumes = deque(maxlen=1000)
        self.running = False
        self.data_file = DATA_FILE
        self.start_time = datetime.now()
        self.data_start_time = None  # Quando iniziamo a raccogliere dati
        self.first_start_time = None  # Primo avvio della demo
        self.level_detector = LevelDetector()
        self.levels_analysis = {'resistance': [], 'support': [], 'current_range': None}
        self.status = {
            'price': 0.0,
            'last_update': 0,
            'signal': 'LOADING',
            'score': 0.0,
            'volatility': 0.0,
            'regime': 'UNKNOWN',
            'confidence': 'LOW',
            'stability': 0.5,
            'data_progress': 0,
            'data_ready': False
        }
        self.console = Console()
        self.load_data()

    def check_lock(self):
        """Controlla se la demo è scaduta (24 ore dal primo avvio)"""
        if os.path.exists(LOCK_FILE):
            try:
                with open(LOCK_FILE, 'r') as f:
                    content = f.read().strip()
                # Controlla se è un hash vecchio (64 caratteri hex) o un timestamp
                if len(content) == 64 and all(c in '0123456789abcdef' for c in content.lower()):
                    # È un hash vecchio, cancella e ricrea
                    print("Rilevato formato lock file vecchio. Migrazione...")
                    os.remove(LOCK_FILE)
                    self.first_start_time = datetime.now()
                    with open(LOCK_FILE, 'w') as f:
                        f.write(self.first_start_time.isoformat())
                    print(f"Primo avvio demo. Durata: {DEMO_DURATION_HOURS} ore")
                else:
                    # È un timestamp valido
                    self.first_start_time = datetime.fromisoformat(content)
                    elapsed = datetime.now() - self.first_start_time
                    if elapsed > timedelta(hours=DEMO_DURATION_HOURS):
                        print(f"\n[DEMO SCADUTA] Sono passate {elapsed.days} giorni e {elapsed.seconds//3600} ore dal primo avvio.")
                        print(f"La demo dura solo {DEMO_DURATION_HOURS} ore totali dal primo avvio.")
                        sys.exit(1)
                    else:
                        remaining = timedelta(hours=DEMO_DURATION_HOURS) - elapsed
                        print(f"Demo attiva. Tempo rimanente: {remaining.days} giorni e {remaining.seconds//3600} ore")
            except Exception as e:
                print(f"Errore lettura lock file: {e}")
                print("Rimuovendo file di blocco corrotto...")
                try:
                    os.remove(LOCK_FILE)
                except:
                    pass
                self.first_start_time = datetime.now()
                with open(LOCK_FILE, 'w') as f:
                    f.write(self.first_start_time.isoformat())
                print(f"Primo avvio demo. Durata: {DEMO_DURATION_HOURS} ore")
        else:
            # Primo avvio: crea file di blocco con timestamp
            self.first_start_time = datetime.now()
            with open(LOCK_FILE, 'w') as f:
                f.write(self.first_start_time.isoformat())
            print(f"Primo avvio demo. Durata: {DEMO_DURATION_HOURS} ore")

    def _get_lock_hash(self):
        s = f"{os.path.abspath(__file__)}|{self.start_time.isoformat()}"
        return hashlib.sha256(s.encode()).hexdigest()

    def fetch_btc(self):
        try:
            r = requests.get(BTC_CONFIG['url'], timeout=8)
            r.raise_for_status()
            data = r.json()
            price = float(data[BTC_CONFIG['price_key']])
            volume = float(data[BTC_CONFIG['volume_key']])
            return price, volume
        except Exception as e:
            print(f"Errore fetch BTC: {e}")
            return None, None

    def calculate_signal(self):
        if len(self.prices) < MIN_DATA_POINTS:
            self.status['signal'] = f'LOADING ({len(self.prices)}/{MIN_DATA_POINTS})'
            self.status['score'] = 0.0
            self.status['confidence'] = 'LOW'
            self.status['data_ready'] = False
            return
        # Dati pronti per l'analisi
        if not self.status['data_ready']:
            self.status['data_ready'] = True
            if self.data_start_time:
                elapsed = datetime.now() - self.data_start_time
                print(f"✅ Dati pronti! Raccolti {MIN_DATA_POINTS} punti in {elapsed}")
        lookback = 20
        price_ago = self.prices[-lookback]
        now = self.prices[-1]
        score = (now - price_ago) / price_ago * 100
        volatility = np.std(list(self.prices)[-20:])
        # Regime semplice
        if volatility > 200:
            regime = 'VOLATILE'
        elif volatility < 50:
            regime = 'CALM'
        else:
            regime = 'MIXED'
        # Confidence
        if abs(score) > 2 and regime == 'CALM':
            confidence = 'HIGH'
        elif abs(score) > 1:
            confidence = 'MEDIUM'
        else:
            confidence = 'LOW'
        # Signal
        if score > 1:
            signal = 'BUY'
        elif score < -1:
            signal = 'SELL'
        else:
            signal = 'HOLD'
        self.status['signal'] = signal
        self.status['score'] = score
        self.status['volatility'] = volatility
        self.status['regime'] = regime
        self.status['confidence'] = confidence
        self.status['stability'] = 1.0 if regime == 'CALM' else 0.5

    def update_levels_analysis(self):
        """Aggiorna analisi livelli resistenza/supporto"""
        if len(self.prices) >= 10:
            prices_list = list(self.prices)
            volumes_list = list(self.volumes) if len(self.volumes) >= len(prices_list) else [1.0] * len(prices_list)
            self.levels_analysis = self.level_detector.detect_levels(prices_list, volumes_list)

    def save_data(self):
        with open(self.data_file, 'wb') as f:
            pickle.dump({
                'prices': self.prices, 
                'volumes': self.volumes, 
                'status': self.status,
                'levels_analysis': self.levels_analysis
            }, f)

    def load_data(self):
        if os.path.exists(self.data_file):
            try:
                with open(self.data_file, 'rb') as f:
                    d = pickle.load(f)
                self.prices = d.get('prices', deque(maxlen=1000))
                self.volumes = d.get('volumes', deque(maxlen=1000))
                self.status = d.get('status', self.status)
                self.levels_analysis = d.get('levels_analysis', self.levels_analysis)
                # Se abbiamo già abbastanza dati, segna come pronti
                if len(self.prices) >= MIN_DATA_POINTS:
                    self.status['data_ready'] = True
            except Exception:
                pass

    def build_table(self):
        table = Table(title="[bold cyan]BTC INTELLIGENCE DEMO - 24 ORE[/bold cyan]", show_header=True, header_style="bold blue")
        table.add_column("CRYPTO", style="bold cyan", width=8)
        table.add_column("PRICE", style="yellow", width=12)
        table.add_column("SIGNAL", style="green", width=15)
        table.add_column("SCORE", style="bright_yellow", width=10)
        table.add_column("REGIME", style="purple", width=10)
        table.add_column("CONF", style="orange1", width=8)
        table.add_column("LEVELS", style="bright_magenta", width=20)
        table.add_column("VOLATILITY", style="blue", width=12)
        table.add_column("STABILITY", style="bright_green", width=10)
        
        price = self.status['price']
        price_str = f"${price:,.2f}" if price else "-"
        signal = self.status['signal']
        
        # Formatta livelli
        levels_str = self._format_levels()
        
        table.add_row(
            "BTC",
            price_str,
            signal,
            f"{self.status['score']:.2f}",
            self.status['regime'],
            self.status['confidence'],
            levels_str,
            f"{self.status['volatility']:.2f}",
            f"{self.status['stability']:.2f}"
        )
        return table

    def _format_levels(self) -> str:
        """Formatta livelli resistenza/supporto per display"""
        try:
            current_range = self.levels_analysis.get('current_range', {})
            resistance = current_range.get('resistance')
            support = current_range.get('support')
            current_price = self.status['price']
            
            if not resistance or not support or current_price == 0:
                return "[grey50]No levels[/grey50]"
            
            # Calcola distanza dai livelli
            distance_to_resistance = abs(current_price - resistance) / resistance
            distance_to_support = abs(current_price - support) / support
            
            # Formatta prezzi
            if resistance > 1000:
                res_str = f"{resistance:,.0f}"
            else:
                res_str = f"{resistance:.2f}"
                
            if support > 1000:
                sup_str = f"{support:,.0f}"
            else:
                sup_str = f"{support:.2f}"
            
            # Determina colori basati su vicinanza
            if distance_to_resistance < 0.005:  # Molto vicino a resistenza
                res_color = "bold red"
            elif distance_to_resistance < 0.01:  # Vicino a resistenza
                res_color = "red"
            else:
                res_color = "grey70"
                
            if distance_to_support < 0.005:  # Molto vicino a supporto
                sup_color = "bold green"
            elif distance_to_support < 0.01:  # Vicino a supporto
                sup_color = "green"
            else:
                sup_color = "grey70"
            
            # Formato compatto: RES/SUP
            return f"[{res_color}]{res_str}[/{res_color}]/[{sup_color}]{sup_str}[/{sup_color}]"
                
        except Exception as e:
            return "[red]Error[/red]"

    def run(self):
        self.check_lock()
        self.running = True
        print("Demo BTC Fedele - SOLO BTC - 24 ORE - NO TELEGRAM - RIAPRIBILE")
        print("Avvio sessione:", self.start_time.strftime('%Y-%m-%d %H:%M:%S'))
        if self.first_start_time:
            print("Primo avvio demo:", self.first_start_time.strftime('%Y-%m-%d %H:%M:%S'))
        print(f"Raccolta dati: {MIN_DATA_POINTS} punti necessari per iniziare l'analisi...")
        
        # Inizializza data_start_time solo se non abbiamo già dati
        if len(self.prices) < MIN_DATA_POINTS:
            self.data_start_time = datetime.now()
        
        layout = Layout()
        layout.split(
            Layout(name="header", size=3),
            Layout(name="main", ratio=1),
            Layout(name="footer", size=2)
        )
        
        with Live(layout, screen=False, refresh_per_second=1, transient=True) as live:
            while self.running:
                now = datetime.now()
                # Controlla se sono passate 24 ore dal primo avvio
                if self.first_start_time and (now - self.first_start_time) > timedelta(hours=DEMO_DURATION_HOURS):
                    print("Demo terminata: 24 ore totali completate.")
                    break
                
                # Fetch dati BTC
                price, volume = self.fetch_btc()
                if price:
                    self.prices.append(price)
                    self.volumes.append(volume)
                    self.status['price'] = price
                    self.status['last_update'] = time.time()
                    self.status['data_progress'] = len(self.prices)
                    self.calculate_signal()
                    self.update_levels_analysis()  # Aggiorna livelli
                
                # Aggiorna layout con refresh forzato
                header_text = "[bold magenta]BTC INTELLIGENCE DEMO - 24 ORE[/bold magenta]"
                if not self.status['data_ready']:
                    if self.data_start_time:
                        elapsed = datetime.now() - self.data_start_time
                        header_text += f" | [yellow]Raccolta dati: {len(self.prices)}/{MIN_DATA_POINTS} ({elapsed})[/yellow]"
                    else:
                        header_text += f" | [yellow]Raccolta dati: {len(self.prices)}/{MIN_DATA_POINTS}[/yellow]"
                else:
                    header_text += " | [green]✅ Dati pronti![/green]"
                
                layout["header"].update(Panel(Align.center(header_text), style="blue"))
                layout["main"].update(self.build_table())
                
                # Footer con tempo rimanente
                footer_text = f"[bold]Sessione:[/] {self.start_time.strftime('%H:%M:%S')} | [bold]Ora:[/] {now.strftime('%H:%M:%S')}"
                if self.first_start_time:
                    total_elapsed = now - self.first_start_time
                    remaining = timedelta(hours=DEMO_DURATION_HOURS) - total_elapsed
                    footer_text += f" | [bold]Rimanenti:[/] {remaining.days}d {remaining.seconds//3600}h {(remaining.seconds%3600)//60}m"
                if self.data_start_time:
                    data_elapsed = now - self.data_start_time
                    footer_text += f" | [bold]Dati:[/] {len(self.prices)} punti in {str(data_elapsed).split('.')[0]}"
                
                layout["footer"].update(Panel(footer_text, style="blue"))
                
                # Salva dati e aspetta
                self.save_data()
                time.sleep(1)  # 1 secondo per datapoint
                
                # Forza refresh del display
                live.refresh()
        
        self.save_data()
        print("Dati salvati in", self.data_file)
        print("DEMO COMPLETATA. 24 ore totali esaurite.")

if __name__ == "__main__":
    demo = DemoBTCFedeleSystem()
    demo.run() 