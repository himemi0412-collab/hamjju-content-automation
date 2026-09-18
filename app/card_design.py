from __future__ import annotations

from copy import deepcopy
from typing import Any


DESIGN_DIRECTION_KEYS = (
    'typography', 'grid', 'visual_motif', 'whitespace', 'color_texture',
)
DESIGN_BLUEPRINT_KEYS = (
    'cover_composition', 'type_system', 'image_strategy',
    'material_treatment', 'role_expansion',
)
CARD_ROLE_KEYS = ('cover', 'flow', 'comparison', 'checklist', 'decision')


def _style(
    palette: tuple[str, str, str, str], *, background: str, surface: str,
    accent_text: str, panel: str, motif: str, title_size: int, copy_size: int,
    radius: int, border_width: int, typography: str, grid: str,
    visual_motif: str, whitespace: str, color_texture: str,
) -> dict[str, Any]:
    return {
        'palette': palette,
        'background': background,
        'surface': surface,
        'accent_text': accent_text,
        'panel': panel,
        'motif': motif,
        'title_size': title_size,
        'copy_size': copy_size,
        'radius': radius,
        'border_width': border_width,
        'direction': {
            'typography': typography,
            'grid': grid,
            'visual_motif': visual_motif,
            'whitespace': whitespace,
            'color_texture': color_texture,
        },
    }


DESIGN_LANGUAGES: dict[str, dict[str, Any]] = {
    'Swiss Typography': _style(
        ('#E34A3F', '#DDE5F1', '#B9D3A8', '#111111'), background='#FFFFFF',
        surface='#F4F5F2', accent_text='#FFFFFF', panel='rule', motif='swiss',
        title_size=70, copy_size=30, radius=0, border_width=3,
        typography='큰 비대칭 산세리프 제목과 작은 정보 라벨',
        grid='엄격한 비대칭 모듈 그리드와 정렬선',
        visual_motif='굵은 기준선, 작은 번호, 단순 기하 도형',
        whitespace='넓은 바깥 여백과 의도적인 빈 칸',
        color_texture='순백 바탕, 검정 타이포, 제한된 빨강·저채도 포인트',
    ),
    'Retro Tech UI': _style(
        ('#3EE6A8', '#8AA4FF', '#EE7FA5', '#DDFBF1'), background='#10151B',
        surface='#18222B', accent_text='#0D1616', panel='window', motif='retro_ui',
        title_size=58, copy_size=28, radius=4, border_width=3,
        typography='모노스페이스처럼 보이는 짧은 제목과 상태 라벨',
        grid='구형 운영체제 창과 상태 패널 구조',
        visual_motif='픽셀 코너, 메뉴 바, 작은 상태 표시등',
        whitespace='창 사이의 어두운 여백을 충분히 유지',
        color_texture='차콜 화면과 민트·블루·핑크의 제한된 UI 포인트',
    ),
    'Bento Editorial': _style(
        ('#C9B8E8', '#A7DDCE', '#F7A6A6', '#202329'), background='#F7F8FA',
        surface='#FFFFFF', accent_text='#202329', panel='bento', motif='bento',
        title_size=62, copy_size=30, radius=18, border_width=2,
        typography='크기 차가 분명한 제목과 짧은 모듈 라벨',
        grid='정보 역할별 크기가 다른 벤토 모듈',
        visual_motif='크고 작은 정보 칸과 기능성 아이콘',
        whitespace='모듈 사이 간격과 카드 바깥 여백을 균일하게 유지',
        color_texture='밝은 중립 바탕과 저채도 파스텔 모듈',
    ),
    'Monochrome Magazine': _style(
        ('#111111', '#D7D7D7', '#8E8E8E', '#111111'), background='#FFFFFF',
        surface='#F2F2F2', accent_text='#FFFFFF', panel='rule', motif='magazine',
        title_size=70, copy_size=29, radius=0, border_width=2,
        typography='잡지 표제처럼 큰 흑백 제목과 작은 캡션',
        grid='한쪽으로 긴 편집 칼럼과 가는 구분선',
        visual_motif='페이지 번호, 세로 규칙선, 사진 자리 같은 면 분할',
        whitespace='흑백 대비를 살리는 넓은 흰 여백',
        color_texture='검정·회색·흰색만 사용하는 무광 편집 인쇄감',
    ),
    'Neo Brutalism': _style(
        ('#FF5D73', '#6C83FF', '#58D6B3', '#111111'), background='#F4F4F4',
        surface='#FFFFFF', accent_text='#111111', panel='brutal', motif='brutal',
        title_size=70, copy_size=30, radius=0, border_width=5,
        typography='굵고 직설적인 제목과 짧은 대문자형 라벨',
        grid='일부러 어긋난 블록과 강한 외곽선',
        visual_motif='검정 그림자, 원색 블록, 큰 번호',
        whitespace='블록 사이 간격은 넓게 두되 긴장감 있게 배치',
        color_texture='무광 원색 포인트와 거친 검정 외곽선',
    ),
    'Japanese Editorial': _style(
        ('#C85B70', '#AFC5DA', '#B7C9B0', '#25252A'), background='#FFFFFF',
        surface='#F5F6F7', accent_text='#FFFFFF', panel='rule', motif='japanese',
        title_size=60, copy_size=29, radius=0, border_width=2,
        typography='절제된 제목과 세로 방향 보조 라벨',
        grid='비대칭 편집면과 가는 세로 기준선',
        visual_motif='세로 인덱스, 작은 원형 표식, 긴 캡션선',
        whitespace='한쪽을 비워 두는 조용한 편집 여백',
        color_texture='흰 바탕과 먹색, 저채도 장미·청회색 포인트',
    ),
    'Quiet Luxury Editorial': _style(
        ('#8D8179', '#C7BFC4', '#AABCB4', '#2D2A2C'), background='#F3F1EF',
        surface='#FCFBFA', accent_text='#FFFFFF', panel='soft', motif='quiet',
        title_size=58, copy_size=28, radius=8, border_width=1,
        typography='얇고 넉넉한 자간의 제목과 작은 캡션',
        grid='정갈한 한두 개의 큰 면과 얇은 구분선',
        visual_motif='작은 인덱스와 절제된 프레임',
        whitespace='가장 넓은 여백과 낮은 정보 밀도',
        color_texture='쿨 그레이지와 저채도 녹회색의 무광 종이감',
    ),
    'Newspaper 2.0': _style(
        ('#D84B45', '#B8C7D9', '#CAD5CE', '#151515'), background='#FFFFFF',
        surface='#F5F5F3', accent_text='#FFFFFF', panel='rule', motif='newspaper',
        title_size=64, copy_size=28, radius=0, border_width=2,
        typography='굵은 헤드라인과 작은 기사형 정보 라벨',
        grid='현대적으로 단순화한 다단 신문 그리드',
        visual_motif='가로 규칙선, 칼럼 번호, 기사 인덱스',
        whitespace='칼럼 사이 여백과 상하 여백을 선명하게 분리',
        color_texture='흑백 인쇄 바탕에 빨강 한 가지 포인트',
    ),
    'Technical Manual': _style(
        ('#3D78B7', '#7FAFD4', '#8EC2B5', '#15324A'), background='#EFF6FA',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='sharp', motif='technical',
        title_size=58, copy_size=28, radius=0, border_width=2,
        typography='도면 제목과 규격 라벨처럼 명확한 계층',
        grid='정밀한 기준선과 치수선 중심의 설계도 그리드',
        visual_motif='눈금, 좌표, 화살표, 부품 번호',
        whitespace='도식 주변에 판독 가능한 안전 여백',
        color_texture='청백색 도면 바탕과 블루·청록 선',
    ),
    'Screenshot Editorial': _style(
        ('#6B7FE8', '#A7DDCE', '#E9A7BD', '#20242C'), background='#EEF1F6',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='window', motif='screenshot',
        title_size=60, copy_size=29, radius=12, border_width=2,
        typography='화면 캡처 위에 얹는 큰 제목과 UI 라벨',
        grid='브라우저 창과 확대 화면을 겹치는 편집 구조',
        visual_motif='창 상단 바, 포커스 프레임, 확대 표시',
        whitespace='화면 가장자리와 주석 사이 여백 확보',
        color_texture='차가운 회색 배경과 선명한 블루·민트 포인트',
    ),
    'Prompt Playground': _style(
        ('#7C5CE6', '#A7DDCE', '#F3A6C0', '#241B35'), background='#F6F3FB',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='window', motif='prompt',
        title_size=60, copy_size=29, radius=14, border_width=2,
        typography='프롬프트 입력문처럼 짧은 제목과 토큰형 라벨',
        grid='입력창·응답창·커서가 이어지는 UI 흐름',
        visual_motif='커서, 입력 필드, 작은 AI 창과 연결선',
        whitespace='창 사이에 탐색 가능한 여백을 유지',
        color_texture='라벤더 바탕과 민트·핑크의 제한된 인터랙션 색',
    ),
    'Terminal Noir': _style(
        ('#4EE2A7', '#57A8FF', '#B48CFF', '#EAF7EF'), background='#090D11',
        surface='#101820', accent_text='#09110D', panel='window', motif='terminal',
        title_size=58, copy_size=27, radius=2, border_width=2,
        typography='터미널 명령처럼 짧고 선명한 제목과 상태값',
        grid='검정 화면 안의 행·열 기반 정보 구조',
        visual_motif='프롬프트 기호, 커서, 로그 인덱스',
        whitespace='검정 여백을 충분히 남겨 코드 밀도를 낮춤',
        color_texture='블랙 바탕과 민트·블루·보라의 작은 발광 포인트',
    ),
    'Fluorescent Minimal': _style(
        ('#39D9B0', '#FF6F91', '#7C8CFF', '#111319'), background='#FFFFFF',
        surface='#F5F6F7', accent_text='#111319', panel='rule', motif='fluorescent',
        title_size=68, copy_size=29, radius=0, border_width=2,
        typography='큰 검정 제목과 형광펜처럼 짧은 강조',
        grid='단순한 한 축 정렬과 최소한의 블록',
        visual_motif='얇은 형광 밑줄과 작은 강조 사각형',
        whitespace='장식보다 빈 공간이 훨씬 많은 구성',
        color_texture='순백 바탕과 민트·코랄·블루의 작은 고채도 포인트',
    ),
    'Soft Swiss': _style(
        ('#8E86D8', '#A7DDCE', '#F4B3C3', '#252631'), background='#FAFAFC',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='soft', motif='soft_swiss',
        title_size=66, copy_size=30, radius=16, border_width=2,
        typography='스위스식 위계에 부드러운 제목 크기 변화',
        grid='정돈된 비대칭 그리드와 둥근 보조 면',
        visual_motif='원·선·번호를 부드럽게 연결한 기하 요소',
        whitespace='넓고 균형 잡힌 여백',
        color_texture='차가운 흰 바탕과 라벤더·민트·로즈 파스텔',
    ),
    'Index / Catalogue': _style(
        ('#5567A8', '#A8C5BD', '#D7B2C3', '#242730'), background='#F4F5F6',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='sharp', motif='index',
        title_size=58, copy_size=28, radius=2, border_width=2,
        typography='카탈로그 표제와 일련번호 중심의 정보 계층',
        grid='목차·색인·분류 탭을 닮은 반복 가능한 그리드',
        visual_motif='인덱스 탭, 번호, 얇은 분류선',
        whitespace='항목 사이 간격을 일정하게 유지',
        color_texture='쿨 그레이 바탕과 네이비·세이지·로즈 탭',
    ),
    'Cinematic Title Card': _style(
        ('#8B72D9', '#5078A8', '#B76D86', '#F3F2F7'), background='#0D1018',
        surface='#171C28', accent_text='#FFFFFF', panel='sharp', motif='cinematic',
        title_size=70, copy_size=28, radius=0, border_width=1,
        typography='영화 타이틀처럼 큰 제목과 작은 크레디트형 정보',
        grid='가로 화면 중심축과 장면 전환 같은 면 분할',
        visual_motif='레터박스, 장면 번호, 빛 띠 대신 단색 면',
        whitespace='제목 주변의 어두운 음영 여백을 크게 확보',
        color_texture='네이비 블랙 바탕과 보라·청색·와인색의 무광 포인트',
    ),
    'Zine Collage': _style(
        ('#D9576B', '#6B83D6', '#65B9A5', '#171717'), background='#F6F6F3',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='tape', motif='zine',
        title_size=64, copy_size=29, radius=0, border_width=3,
        typography='오려 붙인 듯 크기 차가 큰 제목과 손메모 라벨',
        grid='의도적으로 조금 어긋난 종이 조각의 중첩',
        visual_motif='테이프, 찢은 종이 가장자리, 스탬프 번호',
        whitespace='콜라주 사이 숨 쉴 흰 공간을 충분히 남김',
        color_texture='회백색 종이와 코랄·블루·세이지의 무광 인쇄감',
    ),
    'Split Screen': _style(
        ('#5D73D6', '#E17C95', '#79BAAA', '#1F2330'), background='#F7F8FA',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='split', motif='split',
        title_size=62, copy_size=29, radius=4, border_width=2,
        typography='좌우 비교를 읽기 쉬운 큰 제목과 동일 위계 라벨',
        grid='화면을 두 영역으로 나눈 대칭 또는 비대칭 분할',
        visual_motif='중앙 분할선, 양쪽 번호, 비교 화살표',
        whitespace='두 영역 안쪽 여백을 같은 기준으로 유지',
        color_texture='쿨 블루와 로즈의 명확한 두 면 대비',
    ),
    'Chrome Accent': _style(
        ('#5F74D8', '#B8C2D0', '#8FD0C1', '#20242C'), background='#F3F5F8',
        surface='#FFFFFF', accent_text='#FFFFFF', panel='chrome', motif='chrome',
        title_size=62, copy_size=29, radius=14, border_width=2,
        typography='미니멀 제목과 작고 정밀한 캡션',
        grid='단순한 편집 그리드 위에 한두 개의 금속성 포인트',
        visual_motif='겹친 원, 얇은 반사선, 은회색 포인트 오브젝트',
        whitespace='크롬 요소 주변을 넓게 비워 강조',
        color_texture='쿨 화이트·실버·코발트·민트의 절제된 조합',
    ),
    'Modular Poster': _style(
        ('#5168C8', '#D96C82', '#6EB6A5', '#191B22'), background='#FFFFFF',
        surface='#F3F4F5', accent_text='#FFFFFF', panel='modular', motif='modular',
        title_size=68, copy_size=29, radius=0, border_width=3,
        typography='포스터형 대제목과 번호가 붙은 짧은 정보 블록',
        grid='크기가 다른 사각 모듈을 맞물린 포스터 그리드',
        visual_motif='색면 블록, 모듈 번호, 굵은 정렬선',
        whitespace='모듈과 외곽 사이의 여백을 명확히 유지',
        color_texture='순백 바탕과 코발트·코랄·세이지의 평면 색',
    ),
}


SUPPORTED_DESIGN_LANGUAGES = tuple(DESIGN_LANGUAGES)


def _blueprint(
    cover_composition: str, image_strategy: str, material_treatment: str,
    roles: tuple[str, str, str, str, str],
) -> dict[str, Any]:
    return {
        'cover_composition': cover_composition,
        'type_system': '',  # Filled from the canonical style direction below.
        'image_strategy': image_strategy,
        'material_treatment': material_treatment,
        'role_expansion': dict(zip(CARD_ROLE_KEYS, roles)),
    }


# A style name is only the selector.  This cover-first blueprint is the
# production contract that makes the name visible in the finished set.
DESIGN_BLUEPRINTS: dict[str, dict[str, Any]] = {
    'Swiss Typography': _blueprint(
        'One oversized asymmetric headline locked to a strict baseline grid',
        'One functional geometric object or diagram; never an icon collection',
        'Pure white paper, black ink and one flat red accent',
        ('hero word and one object', 'numbered linear sequence', 'aligned two-column comparison',
         'baseline checklist', 'single-rule poster conclusion'),
    ),
    'Retro Tech UI': _blueprint(
        'A dominant old-computer window with a compact headline inside its chrome',
        'Pixel-like windows, status bars and cursor states that explain the topic',
        'Dark CRT charcoal, phosphor mint and restrained blue-pink pixels',
        ('boot screen', 'stacked program windows', 'two legacy panes', 'system status list', 'final command screen'),
    ),
    'Bento Editorial': _blueprint(
        'One large story tile supported by two small factual tiles',
        'Each module contains a distinct object, crop or diagram with a clear job',
        'Cool white stock with lavender, mint and coral matte blocks',
        ('hero tile', 'three unequal process tiles', 'matched comparison tiles', 'vertical action modules', 'one dominant verdict tile'),
    ),
    'Monochrome Magazine': _blueprint(
        'Full-page black-and-white cover with a large masthead and one image field',
        'High-contrast photograph-like crop or silhouette plus editorial captions',
        'Matte black ink, gray halftone and uncoated white paper',
        ('cover story', 'captioned photo sequence', 'facing-page comparison', 'editorial index', 'closing full-page quote'),
    ),
    'Neo Brutalism': _blueprint(
        'Oversized boxed headline colliding with one blunt object',
        'Hard-edged blocks, arrows and content objects with visible black outlines',
        'Raw white stock, black keylines and two saturated flat colors',
        ('impact poster', 'offset process blocks', 'hard split comparison', 'stamped checklist', 'oversized verdict'),
    ),
    'Japanese Editorial': _blueprint(
        'Quiet image field with restrained headline and a vertical index rail',
        'One tactile object or calm scene crop supported by fine rules and captions',
        'Soft white paper, ink black and muted rose-blue-gray accents',
        ('quiet cover', 'vertical reading sequence', 'balanced facing comparison', 'indexed notes', 'minimal closing page'),
    ),
    'Quiet Luxury Editorial': _blueprint(
        'Low-density cover with one premium object and widely spaced title',
        'One refined material or still-life object per card; no icon clusters',
        'Cool greige paper, subtle grain, soft natural shadow and hairline rules',
        ('still-life cover', 'slow sequence', 'paired material study', 'minimal audit list', 'one-line conclusion'),
    ),
    'Newspaper 2.0': _blueprint(
        'A modern front page with one main headline, one image and clear columns',
        'News photograph-like crop, data rule and short column hierarchy',
        'Neutral newsprint, black ink and a single red or green signal color',
        ('front page', 'three-column explainer', 'fact-table comparison', 'service checklist', 'editorial verdict'),
    ),
    'Technical Manual': _blueprint(
        'A hero technical object surrounded by sparse measurement callouts',
        'Blueprint lines, exploded parts, scale marks and functional arrows',
        'Cool drafting paper, navy linework and restrained cyan-green markers',
        ('assembly cover', 'numbered process drawing', 'same-scale comparison', 'inspection sheet', 'decision schematic'),
    ),
    'Screenshot Editorial': _blueprint(
        'One large angled app screen or device dominates the image field; oversized title sits in a separate editorial zone',
        'A specific code, chat, file, terminal or history screen for every card; use one meaningful screen scene instead of generic icons',
        'Cool daylight workspace, paper-white margin, soft screen shadow, subtle print grain, charcoal with cobalt and mint accents',
        ('hero screen plus lower title field', 'zoomed code/file screen sequence', 'dark-code versus bright-chat screen comparison',
         'one screen with three annotated focus zones', 'decisive dark-light split screen with one final rule'),
    ),
    'Prompt Playground': _blueprint(
        'An active prompt field and cursor lead into one visible response space',
        'Prompt windows, cursor paths and response layers that show interaction',
        'Lavender paper, crisp white UI, mint and pink interaction accents',
        ('prompt hero', 'input-response flow', 'two prompt paths', 'prompt checklist', 'final reusable prompt'),
    ),
    'Terminal Noir': _blueprint(
        'A black terminal fills most of the cover with one luminous command line',
        'Terminal rows, logs, cursors and status output; no decorative app tiles',
        'Near-black screen, matte grain, mint-blue-violet monospace signals',
        ('command cover', 'log sequence', 'two terminal sessions', 'status audit', 'successful final command'),
    ),
    'Fluorescent Minimal': _blueprint(
        'Large black headline on white with one fluorescent intervention',
        'One small object or line diagram and one highlighter stroke per card',
        'Pure white stock, black ink and tiny neon-mint or coral accents',
        ('headline cover', 'single-axis steps', 'minimal paired comparison', 'highlighted action list', 'one fluorescent conclusion'),
    ),
    'Soft Swiss': _blueprint(
        'Asymmetric Swiss headline softened by one translucent geometric field',
        'Simple geometric relationships and one content object per card',
        'Cool white, lavender, mint and rose with soft matte edges',
        ('soft grid cover', 'rounded geometric flow', 'balanced paired fields', 'calm checklist', 'large final statement'),
    ),
    'Index / Catalogue': _blueprint(
        'A numbered catalogue cover with one specimen and visible classification tabs',
        'Indexed objects, category rails and consistent specimen views',
        'Cool gray catalogue paper, navy type and sage-rose tabs',
        ('catalogue cover', 'indexed sequence', 'specimen comparison', 'tabbed checklist', 'selection index'),
    ),
    'Cinematic Title Card': _blueprint(
        'A wide atmospheric image band with title-card typography and quiet credits',
        'One cinematic scene or symbolic object with controlled crop and scale',
        'Navy-black matte image, restrained violet-blue-wine grade, no glossy flare',
        ('opening title', 'three scene progression', 'parallel scene comparison', 'cue-sheet checklist', 'closing title'),
    ),
    'Zine Collage': _blueprint(
        'A torn-paper headline collides with one documentary image fragment',
        'Original paper scraps, tape, photocopy texture and functional handwritten marks',
        'Off-white recycled paper, charcoal photocopy and coral-blue-sage ink',
        ('collage cover', 'layered sequence', 'two torn-page comparison', 'stamped checklist', 'final pasted note'),
    ),
    'Split Screen': _blueprint(
        'Two opposing visual fields meet at one clear decision boundary',
        'Two matched scenes or objects shown at the same scale and angle',
        'Cool white with distinct blue and rose fields plus one mint connector',
        ('split cover', 'left-to-right route', 'true matched comparison', 'dual-path checklist', 'single boundary decision'),
    ),
    'Chrome Accent': _blueprint(
        'Minimal headline and one isolated chrome object with generous negative space',
        'One precise object or screen crop reflected in a restrained metal accent',
        'Cool white and silver, soft gray shadow, cobalt and mint micro accents',
        ('chrome hero', 'reflection-led flow', 'paired object study', 'precision checklist', 'single polished conclusion'),
    ),
    'Modular Poster': _blueprint(
        'Large interlocking title blocks form the cover image',
        'Content-specific image crops and diagrams occupy differently sized poster modules',
        'Pure white stock, black keylines and cobalt-coral-sage flat ink',
        ('module cover', 'stepped modular flow', 'two-weight comparison', 'stacked action modules', 'one merged conclusion block'),
    ),
}

for _name, _blueprint_value in DESIGN_BLUEPRINTS.items():
    _blueprint_value['type_system'] = DESIGN_LANGUAGES[_name]['direction']['typography']


def get_design_language(name: str) -> dict[str, Any]:
    try:
        return DESIGN_LANGUAGES[name]
    except KeyError as exc:
        raise ValueError(f'Unsupported named card-news design language: {name!r}') from exc


def get_design_blueprint(name: str) -> dict[str, Any]:
    get_design_language(name)
    return deepcopy(DESIGN_BLUEPRINTS[name])


def apply_named_design_contract(generated: dict[str, Any]) -> dict[str, Any]:
    """Bind an AI-selected style name to inspectable deterministic render tokens."""
    name = generated.get('design_language')
    style = get_design_language(name)
    result = deepcopy(generated)
    result['design_direction'] = deepcopy(style['direction'])
    result['design_blueprint'] = get_design_blueprint(name)
    return result


def validate_named_design_contract(generated: dict[str, Any], *, required: bool) -> None:
    name = generated.get('design_language')
    direction = generated.get('design_direction')
    blueprint = generated.get('design_blueprint')
    if name is None and direction is None and blueprint is None and not required:
        return
    if name not in DESIGN_LANGUAGES:
        raise RuntimeError('REFERENCE_DESIGN_LANGUAGE_INVALID')
    expected = DESIGN_LANGUAGES[name]['direction']
    if not isinstance(direction, dict) or tuple(direction) != DESIGN_DIRECTION_KEYS:
        raise RuntimeError('REFERENCE_DESIGN_DIRECTION_INVALID')
    if direction != expected:
        raise RuntimeError('REFERENCE_DESIGN_DIRECTION_MISMATCH')
    if blueprint is None and not required:
        # Preserve already-reviewed bytes created before the cover-first
        # blueprint contract existed. Fresh work must always include it.
        return
    if not isinstance(blueprint, dict) or tuple(blueprint) != DESIGN_BLUEPRINT_KEYS:
        raise RuntimeError('REFERENCE_DESIGN_BLUEPRINT_INVALID')
    expected_blueprint = get_design_blueprint(name)
    if blueprint != expected_blueprint:
        raise RuntimeError('REFERENCE_DESIGN_BLUEPRINT_MISMATCH')
    roles = blueprint.get('role_expansion')
    if not isinstance(roles, dict) or tuple(roles) != CARD_ROLE_KEYS:
        raise RuntimeError('REFERENCE_DESIGN_ROLE_EXPANSION_INVALID')
    if len(set(roles.values())) != len(CARD_ROLE_KEYS):
        raise RuntimeError('REFERENCE_DESIGN_ROLE_EXPANSION_REPEATED')
