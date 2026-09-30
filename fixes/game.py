import random
from array import array

import pygame

WIDTH, HEIGHT = 800, 600
GROUND_Y = HEIGHT - 40
INTERCEPTOR_SPEED, EXPLOSION_MAX, EXPLOSION_TIME = 420, 45, 1.2
AMMO_PER_BATTERY = 10

# NEW: tunables for the added features
REPAIR_SCORE = 2000        # score interval that rebuilds one city
SHAKE_TIME = 0.4           # seconds of screen shake per city loss
SHAKE_MAX_OFFSET = 8       # max pixel offset at full shake
LAST_CITY_TIME = 2.0       # seconds the "LAST CITY!" warning is shown

# NEW: module-level state, because on_city_destroyed() has no access to Game/dt
shake_time = 0.0

# Keyframes for explosion_color: (progress, (r, g, b)). Radius peaks at 0.5 -> white.
_EXPLOSION_KEYS = (
    (0.0, (255, 150, 30)),
    (0.5, (255, 255, 255)),
    (1.0, (170, 20, 10)),
)


def explosion_color(progress):
    """Return an (r, g, b) colour for an explosion (progress 0..1 of its life), or None for the default."""
    # IMPLEMENTED: orange -> white -> deep red, linear between keyframes, input clamped.
    try:
        p = float(progress)
    except (TypeError, ValueError):
        p = 0.0
    if p != p:  # NaN
        p = 0.0
    p = max(0.0, min(1.0, p))
    for (p0, c0), (p1, c1) in zip(_EXPLOSION_KEYS, _EXPLOSION_KEYS[1:]):
        if p <= p1:
            t = (p - p0) / (p1 - p0)
            return tuple(max(0, min(255, int(round(a + (b - a) * t)))) for a, b in zip(c0, c1))
    return _EXPLOSION_KEYS[-1][1]


def _beep():
    """Short square-wave warning beep; silently does nothing without a usable mixer."""
    init = pygame.mixer.get_init()
    if not init or init[1] != -16:
        return
    rate, _, channels = init
    half = max(1, rate // 880)  # ~440 Hz
    samples = array("h", [(6000 if (i // half) % 2 else -6000)
                          for i in range(int(rate * 0.12)) for _ in range(channels)])
    pygame.mixer.Sound(buffer=samples.tobytes()).play()


def on_city_destroyed(city):
    """Called when a city is hit; add screen shake, sounds, or a game-over warning here."""
    # IMPLEMENTED: start a shake (never shortening a running one) and try a beep. Never raises.
    global shake_time
    try:
        shake_time = max(shake_time, SHAKE_TIME)
        _beep()
    except Exception:
        pass


def city_repair_threshold():
    """Return a score value at which a destroyed city is rebuilt, or None to disable city repair."""
    # IMPLEMENTED: positive int via module constant.
    return REPAIR_SCORE


class Battery:
    def __init__(self, x):
        self.pos = pygame.Vector2(x, GROUND_Y)
        self.ammo = AMMO_PER_BATTERY
        self.alive = True


class City:
    def __init__(self, x):
        self.pos = pygame.Vector2(x, GROUND_Y)
        self.alive = True


class Interceptor:
    def __init__(self, origin, target):
        self.pos = pygame.Vector2(origin)
        self.origin = pygame.Vector2(origin)
        self.target = pygame.Vector2(target)

    def update(self, dt):
        """Advance toward the target; return True once the warhead should detonate."""
        # FIX 2: arrive exactly on the target. If this frame's step covers the remaining
        # distance (or we're already there), snap to the target instead of overshooting.
        # This also avoids normalize() on a zero-length vector.
        offset = self.target - self.pos
        dist = offset.length()
        step = INTERCEPTOR_SPEED * dt
        if dist <= max(step, 1e-9):
            self.pos = pygame.Vector2(self.target)
            return True
        self.pos += offset * (step / dist)
        return False


class Explosion:
    def __init__(self, pos, max_radius=EXPLOSION_MAX):
        self.pos = pygame.Vector2(pos)
        self.max_radius = max_radius
        self.age = 0.0

    @property
    def progress(self):
        return self.age / EXPLOSION_TIME

    @property
    def radius(self):
        return self.max_radius * (1 - abs(2 * self.progress - 1))

    @property
    def done(self):
        return self.age >= EXPLOSION_TIME


class Missile:
    def __init__(self, target, speed):
        self.origin = pygame.Vector2(random.randint(20, WIDTH - 20), 0)
        self.pos = pygame.Vector2(self.origin)
        self.target = target
        self.velocity = (target.pos - self.origin).normalize() * speed

    def update(self, dt):
        self.pos += self.velocity * dt
        return self.pos.y >= GROUND_Y - 4


class Game:
    def __init__(self):
        self.font = pygame.font.Font(None, 26)
        self._canvas = pygame.Surface((WIDTH, HEIGHT))  # NEW: reusable off-screen surface for shake
        self.reset()

    def reset(self):
        global shake_time
        shake_time = 0.0  # NEW: clear any running shake
        self.warning_time = 0.0  # NEW: clear "LAST CITY!" warning
        self.batteries = [Battery(60), Battery(WIDTH / 2), Battery(WIDTH - 60)]
        xs = [150, 230, 310, 490, 570, 650]
        self.cities = [City(x) for x in xs]
        self.score, self.wave, self.state = 0, 1, "play"
        self.repairs_awarded = 0
        self.start_wave()

    def start_wave(self):
        self.missiles, self.interceptors, self.explosions = [], [], []
        self.to_spawn = 6 + self.wave * 2
        self.spawn_timer = 1.0
        for battery in self.batteries:
            battery.alive, battery.ammo = True, AMMO_PER_BATTERY

    def nearest_battery(self, target):
        # FIX 1: only consider usable batteries (alive with ammo); None if there are none.
        usable = [b for b in self.batteries if b.alive and b.ammo > 0]
        if not usable:
            return None
        return min(usable, key=lambda b: b.pos.distance_squared_to(target))

    def launch(self, target):
        target = pygame.Vector2(target)
        if self.state != "play" or target.y > GROUND_Y - 20:
            return
        battery = self.nearest_battery(target)
        if battery is not None:  # FIX 1: nearest_battery already guarantees alive + ammo
            battery.ammo -= 1
            self.interceptors.append(Interceptor(battery.pos, target))

    def spawn_missile(self):
        targets = [c for c in self.cities if c.alive] + [b for b in self.batteries if b.alive]
        if targets:
            self.missiles.append(Missile(random.choice(targets), 45 + self.wave * 6))

    def update(self, dt):
        # NEW: decay timers first so they keep running after a loss (before the early return).
        global shake_time
        shake_time = max(0.0, shake_time - dt)
        self.warning_time = max(0.0, self.warning_time - dt)
        if self.state != "play":
            return
        threshold = city_repair_threshold()
        # FIX 3: only accept a positive int (bool excluded); process every earned repair,
        # one city each, instead of jumping repairs_awarded to the new total.
        if isinstance(threshold, int) and not isinstance(threshold, bool) and threshold > 0:
            earned = self.score // threshold
            while self.repairs_awarded < earned:
                self.repairs_awarded += 1
                for city in self.cities:
                    if not city.alive:
                        city.alive = True
                        break
        self.spawn_timer -= dt
        if self.to_spawn > 0 and self.spawn_timer <= 0:
            self.spawn_missile()
            self.to_spawn -= 1
            self.spawn_timer = random.uniform(0.6, 1.6)
        for interceptor in self.interceptors[:]:
            if interceptor.update(dt):
                self.interceptors.remove(interceptor)
                # FIX 2: detonate exactly at the clicked target.
                self.explosions.append(Explosion(interceptor.target))
        for explosion in self.explosions:
            explosion.age += dt
            for missile in self.missiles[:]:
                if missile.pos.distance_squared_to(explosion.pos) < explosion.radius ** 2:
                    self.missiles.remove(missile)
                    self.score += 25
        self.explosions = [e for e in self.explosions if not e.done]
        for missile in self.missiles[:]:
            if missile.update(dt):
                self.missiles.remove(missile)
                self.impact(missile)
        # Guard: never finish a wave on the frame the game was lost.
        if self.state == "play" and not self.missiles and self.to_spawn == 0 and not self.explosions:
            self.finish_wave()

    def impact(self, missile):
        target = missile.target
        if target.alive:
            target.alive = False
            if isinstance(target, City):
                on_city_destroyed(target)
                # NEW: warn when exactly one city remains.
                if sum(c.alive for c in self.cities) == 1:
                    self.warning_time = LAST_CITY_TIME
            elif isinstance(target, Battery):
                target.ammo = 0  # FIX 4: a dead battery holds no ammo
        self.explosions.append(Explosion(missile.pos, 30))
        if not any(c.alive for c in self.cities):
            self.state = "lose"

    def finish_wave(self):
        # FIX 4: only living batteries pay out for leftover ammo.
        self.score += 100 * sum(c.alive for c in self.cities) + 5 * sum(b.ammo for b in self.batteries if b.alive)
        self.wave += 1
        self.start_wave()

    def draw(self, screen):
        # NEW: render the scene off-screen, then blit with a shake offset that fades with shake_time.
        self._draw_scene(self._canvas)
        screen.fill((5, 5, 25))
        ox = oy = 0
        amp = SHAKE_MAX_OFFSET * min(1.0, shake_time / SHAKE_TIME)
        if amp >= 1:
            r = int(amp)
            ox, oy = random.randint(-r, r), random.randint(-r, r)
        screen.blit(self._canvas, (ox, oy))

    def _draw_scene(self, screen):
        screen.fill((5, 5, 25))
        pygame.draw.rect(screen, (150, 110, 50), (0, GROUND_Y, WIDTH, HEIGHT - GROUND_Y))
        for city in self.cities:
            if city.alive:
                for i, h in enumerate((18, 28, 22)):
                    pygame.draw.rect(screen, (90, 190, 230), (city.pos.x - 18 + i * 12, GROUND_Y - h, 10, h))
        for battery in self.batteries:
            if battery.alive:
                x = battery.pos.x
                pygame.draw.polygon(screen, (220, 220, 80), [(x - 22, GROUND_Y), (x + 22, GROUND_Y), (x, GROUND_Y - 24)])
                label = self.font.render(str(battery.ammo), True, (20, 20, 20))
                screen.blit(label, label.get_rect(center=(x, GROUND_Y + 14)))
        for missile in self.missiles:
            pygame.draw.line(screen, (200, 60, 60), missile.origin, missile.pos, 1)
            pygame.draw.circle(screen, (255, 255, 255), missile.pos, 3)
        for interceptor in self.interceptors:
            pygame.draw.line(screen, (80, 180, 255), interceptor.origin, interceptor.pos, 1)
            pygame.draw.circle(screen, (80, 180, 255), interceptor.target, 5, 1)
        for explosion in self.explosions:
            fade = 1 - explosion.progress * 0.5
            color = explosion_color(explosion.progress) or (int(255 * fade), int(200 * fade), 60)
            pygame.draw.circle(screen, color, explosion.pos, max(1, int(explosion.radius)))
        hud = self.font.render(f"Score {self.score}   Wave {self.wave}   Click to fire   R = reset", True, (240, 240, 240))
        screen.blit(hud, (10, 8))
        # NEW: blinking "LAST CITY!" warning while exactly one city is left.
        if (self.state == "play" and self.warning_time > 0
                and sum(c.alive for c in self.cities) == 1 and int(self.warning_time * 4) % 2 == 0):
            warn = self.font.render("LAST CITY!", True, (255, 80, 80))
            screen.blit(warn, warn.get_rect(center=(WIDTH // 2, 44)))
        if self.state == "lose":
            label = self.font.render("ALL CITIES LOST - Press R", True, (255, 255, 120))
            screen.blit(label, label.get_rect(center=(WIDTH // 2, HEIGHT // 2)))


def main():
    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption("Missile Command")
    clock = pygame.time.Clock()
    game = Game()
    running = True
    while running:
        dt = min(clock.tick(60) / 1000, 0.05)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                game.launch(event.pos)
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_r:
                game.reset()
        game.update(dt)
        game.draw(screen)
        pygame.display.flip()
    pygame.quit()


if __name__ == "__main__":
    main()
