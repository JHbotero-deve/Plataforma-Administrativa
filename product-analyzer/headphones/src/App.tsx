"use client";

import { AnimatePresence, motion } from "motion/react";
import { useEffect, useState } from "react";
import {
  Check,
  ChevronRight,
  Edit3,
  ExternalLink,
  GripVertical,
  Palette,
  PanelRightClose,
  PanelRightOpen,
  Plus,
  Star,
  Trash2,
  Upload,
  X,
  Link2,
  Eye,
  EyeOff,
} from "lucide-react";

// ─── Types ─────────────────────────────────────────────────────
interface ColorVariant {
  id: string;
  name: string;
  image: string;
  gradient: string;
  glow: string;
}

interface Product {
  id: string;
  name: string;
  brand: string;
  description: string;
  features: string[];
  basePrice: number;
  originalPrice: number;
  rating: number;
  reviews: number;
  badge?: string;
  categoryId: string;
  variants: ColorVariant[];
  amazon?: string;
  aliexpress?: string;
  wompi?: string;
  published: boolean;
}

interface Category {
  id: string;
  label: string;
  emoji: string;
  accentGradient: string;
  glowColor: string;
}

// ─── Static config ──────────────────────────────────────────────
const CATEGORIES: Category[] = [
  { id: "zapatos",      label: "Zapatos",      emoji: "👟", accentGradient: "from-orange-500 to-red-600",   glowColor: "rgba(249,115,22,0.18)"  },
  { id: "camisetas",    label: "Camisetas",    emoji: "👕", accentGradient: "from-purple-500 to-pink-600",  glowColor: "rgba(168,85,247,0.18)"  },
  { id: "electronicos", label: "Electrónicos", emoji: "⚡", accentGradient: "from-blue-500 to-cyan-500",    glowColor: "rgba(59,130,246,0.18)"  },
  { id: "accesorios",   label: "Accesorios",   emoji: "💎", accentGradient: "from-amber-500 to-yellow-500", glowColor: "rgba(245,158,11,0.18)"  },
  { id: "hogar",        label: "Hogar",        emoji: "🏠", accentGradient: "from-teal-500 to-green-600",   glowColor: "rgba(20,184,166,0.18)"  },
  { id: "otros",        label: "Otros",        emoji: "📦", accentGradient: "from-slate-400 to-slate-600",  glowColor: "rgba(100,116,139,0.18)" },
];

const BG_THEMES = [
  { id: "navy", label: "Azul noche", color: "#101720" },
  { id: "slate", label: "Grafito", color: "#171f29" },
  { id: "black", label: "Negro", color: "#080b10" },
  { id: "charcoal", label: "Pizarra", color: "#202a35" },
];

const GRAD_PRESETS = [
  { id: "og", label: "Naranja-Rojo",   value: "from-orange-500 to-red-600",   glow: "#ef4444" },
  { id: "pp", label: "Púrpura-Rosa",   value: "from-purple-500 to-pink-600",  glow: "#ec4899" },
  { id: "bc", label: "Azul-Cian",      value: "from-blue-500 to-cyan-500",    glow: "#06b6d4" },
  { id: "ge", label: "Verde",          value: "from-green-400 to-emerald-600", glow: "#10b981" },
  { id: "am", label: "Ámbar",          value: "from-amber-400 to-orange-500", glow: "#f59e0b" },
  { id: "wh", label: "Blanco-Gris",    value: "from-gray-200 to-white",       glow: "#9ca3af" },
  { id: "sb", label: "Pizarra-Negro",  value: "from-slate-500 to-gray-900",   glow: "#64748b" },
  { id: "rr", label: "Rosa-Rojo",      value: "from-rose-500 to-red-700",     glow: "#f43f5e" },
];

const mkId = () => `${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;

function isSafeExternalUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" || url.protocol === "http:";
  } catch {
    return false;
  }
}

function isSafeImageSource(value: string) {
  return /^data:image\/(?:png|jpeg|webp);base64,/i.test(value) || isSafeExternalUrl(value);
}

function readStoredProducts(): Product[] {
  try {
    const raw = window.localStorage.getItem("sp-products");
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.flatMap((value): Product[] => {
      if (!value || typeof value !== "object") return [];
      const item = value as Record<string, unknown>;
      if (typeof item.id !== "string" || typeof item.name !== "string" || !Array.isArray(item.variants)) return [];
      const variants = item.variants.flatMap((entry, index): ColorVariant[] => {
        if (!entry || typeof entry !== "object") return [];
        const variant = entry as Record<string, unknown>;
        if (typeof variant.image !== "string" || !isSafeImageSource(variant.image)) return [];
        return [{
          id: typeof variant.id === "string" ? variant.id : `${item.id}-${index}`,
          name: typeof variant.name === "string" ? variant.name : "Variante",
          image: variant.image,
          gradient: typeof variant.gradient === "string" ? variant.gradient : GRAD_PRESETS[0].value,
          glow: typeof variant.glow === "string" ? variant.glow : GRAD_PRESETS[0].glow,
        }];
      });
      if (!variants.length) return [];
      const numberOr = (candidate: unknown, fallback: number) => {
        const number = Number(candidate);
        return Number.isFinite(number) ? number : fallback;
      };
      return [{
        id: item.id,
        name: item.name,
        brand: typeof item.brand === "string" ? item.brand : "",
        description: typeof item.description === "string" ? item.description : "",
        features: Array.isArray(item.features) ? item.features.filter((feature): feature is string => typeof feature === "string").slice(0, 4) : [],
        basePrice: numberOr(item.basePrice, 0),
        originalPrice: numberOr(item.originalPrice, 0),
        rating: Math.min(5, Math.max(0, numberOr(item.rating, 4.5))),
        reviews: Math.max(0, Math.floor(numberOr(item.reviews, 0))),
        badge: typeof item.badge === "string" ? item.badge : undefined,
        categoryId: CATEGORIES.some(category => category.id === item.categoryId) ? item.categoryId as string : "otros",
        variants,
        amazon: typeof item.amazon === "string" && isSafeExternalUrl(item.amazon) ? item.amazon : undefined,
        aliexpress: typeof item.aliexpress === "string" && isSafeExternalUrl(item.aliexpress) ? item.aliexpress : undefined,
        wompi: typeof item.wompi === "string" && isSafeExternalUrl(item.wompi) ? item.wompi : undefined,
        published: item.published === true,
      }];
    });
  } catch {
    return [];
  }
}

function newEmptyForm() {
  return {
    name: "", brand: "", description: "",
    features: ["", "", "", ""],
    basePrice: "", originalPrice: "",
    rating: "4.5", reviews: "0",
    badge: "", categoryId: "otros",
    variants: [{ id: mkId(), name: "", image: "", gradient: GRAD_PRESETS[0].value, glow: GRAD_PRESETS[0].glow }] as ColorVariant[],
    amazon: "", aliexpress: "", wompi: "",
  };
}

// ─── Main App ───────────────────────────────────────────────────
export default function App() {
  // Showcase state
  const [products, setProducts] = useState<Product[]>(readStoredProducts);
  const [activeCatId, setActiveCatId] = useState("otros");
  const [activeProdId, setActiveProdId] = useState("");
  const [activeVarIdx, setActiveVarIdx] = useState(0);
  const [bgTheme, setBgTheme] = useState(() => {
    try { const stored = localStorage.getItem("sp-bg"); return BG_THEMES.some(theme => theme.id === stored) ? stored as string : "navy"; } catch { return "navy"; }
  });

  // Panel state
  const [panelOpen, setPanelOpen] = useState(
    () => typeof window === "undefined" || window.matchMedia("(min-width: 1024px)").matches,
  );
  const [panelTab, setPanelTab] = useState<"productos" | "agregar" | "apariencia">("productos");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [isDragOver, setIsDragOver] = useState(false);
  const [form, setForm] = useState(newEmptyForm);
  const [storageStatus, setStorageStatus] = useState<"saved" | "error">("saved");
  const [formError, setFormError] = useState("");

  // Persistence
  useEffect(() => {
    try {
      localStorage.setItem("sp-products", JSON.stringify(products));
      setStorageStatus("saved");
    } catch {
      setStorageStatus("error");
    }
  }, [products]);
  useEffect(() => {
    try { localStorage.setItem("sp-bg", bgTheme); } catch {}
  }, [bgTheme]);

  // Derived
  const published = products.filter(p => p.published);
  const activeCatsWithProducts = CATEGORIES.filter(c => published.some(p => p.categoryId === c.id));
  const activeCat = CATEGORIES.find(c => c.id === activeCatId) ?? CATEGORIES[0];
  const catProducts = published.filter(p => p.categoryId === activeCatId);
  const activeProd = catProducts.find(p => p.id === activeProdId) ?? catProducts[0];
  const activeVar = activeProd?.variants[activeVarIdx] ?? activeProd?.variants[0];
  const bgColor = BG_THEMES.find(t => t.id === bgTheme)?.color ?? "#000000";
  const discount = activeProd && activeProd.originalPrice > 0 && activeProd.originalPrice > activeProd.basePrice
    ? Math.round(((activeProd.originalPrice - activeProd.basePrice) / activeProd.originalPrice) * 100)
    : 0;

  useEffect(() => {
    if (published.length && !published.some(product => product.categoryId === activeCatId)) {
      setActiveCatId(published[0].categoryId);
      setActiveProdId(published[0].id);
      setActiveVarIdx(0);
    }
  }, [products, activeCatId]);

  // Manejadores de la tienda
  const goCat = (catId: string) => {
    const prods = published.filter(p => p.categoryId === catId);
    if (!prods.length) return;
    setActiveCatId(catId);
    setActiveProdId(prods[0].id);
    setActiveVarIdx(0);
  };

  const goProd = (prodId: string) => {
    if (prodId === activeProdId) return;
    setActiveProdId(prodId);
    setActiveVarIdx(0);
  };

  // Manejadores de productos
  const togglePublished = (id: string) => {
    const product = products.find(p => p.id === id);
    if (!product) return;
    setProducts(prev => prev.map(p => p.id === id ? { ...p, published: !p.published } : p));
    if (product.published && id === activeProdId) {
      const fallback = products.find(p => p.id !== id && p.published);
      if (fallback) { setActiveCatId(fallback.categoryId); setActiveProdId(fallback.id); setActiveVarIdx(0); }
    }
  };

  const deleteProduct = (id: string) => {
    const fallback = products.find(p => p.id !== id && p.published);
    setProducts(prev => prev.filter(p => p.id !== id));
    if (id === activeProdId && fallback) {
      setActiveCatId(fallback.categoryId);
      setActiveProdId(fallback.id);
      setActiveVarIdx(0);
    }
  };

  const startEditing = (p: Product) => {
    setFormError("");
    setForm({
      name: p.name, brand: p.brand, description: p.description,
      features: [...p.features, "", "", ""].slice(0, 4),
      basePrice: String(p.basePrice), originalPrice: String(p.originalPrice),
      rating: String(p.rating), reviews: String(p.reviews),
      badge: p.badge ?? "", categoryId: p.categoryId,
      variants: p.variants.map(v => ({ ...v })),
      amazon: p.amazon ?? "", aliexpress: p.aliexpress ?? "", wompi: p.wompi ?? "",
    });
    setEditingId(p.id);
    setPanelTab("agregar");
  };

  const cancelForm = () => { setForm(newEmptyForm()); setEditingId(null); setPanelTab("productos"); setFormError(""); };

  const submitForm = () => {
    setFormError("");
    const variants = form.variants.filter(variant => variant.name.trim() && isSafeImageSource(variant.image));
    const basePrice = Number(form.basePrice);
    const originalPrice = form.originalPrice.trim() ? Number(form.originalPrice) : 0;
    const rating = form.rating.trim() ? Number(form.rating) : 4.5;
    const reviews = form.reviews.trim() ? Number(form.reviews) : 0;
    const links = [form.amazon, form.aliexpress, form.wompi].filter(Boolean);

    if (!form.name.trim()) return setFormError("Escribe el nombre del producto.");
    if (!variants.length) return setFormError("Agrega al menos una variante con imagen válida.");
    if (!Number.isFinite(basePrice) || basePrice <= 0) return setFormError("El precio debe ser un número mayor que cero.");
    if (!Number.isFinite(originalPrice) || originalPrice < 0 || (originalPrice > 0 && originalPrice < basePrice)) return setFormError("El precio original debe ser cero o igual/superior al precio actual.");
    if (!Number.isFinite(rating) || rating < 0 || rating > 5) return setFormError("La calificación debe estar entre 0 y 5.");
    if (!Number.isInteger(reviews) || reviews < 0) return setFormError("El número de reseñas debe ser un entero igual o mayor que cero.");
    if (links.some(link => !isSafeExternalUrl(link))) return setFormError("Los enlaces de compra deben comenzar con http:// o https://.");

    const prod: Product = {
      id: editingId ?? mkId(),
      name: form.name.trim(), brand: form.brand.trim(), description: form.description.trim(),
      features: form.features.filter(f => f.trim()),
      basePrice, originalPrice, rating, reviews,
      badge: form.badge.trim() || undefined,
      categoryId: form.categoryId,
      variants,
      amazon: form.amazon.trim() || undefined,
      aliexpress: form.aliexpress.trim() || undefined,
      wompi: form.wompi.trim() || undefined,
      published: true,
    };
    setProducts(prev => editingId ? prev.map(p => p.id === editingId ? prod : p) : [...prev, prod]);
    setActiveCatId(prod.categoryId);
    setActiveProdId(prod.id);
    setActiveVarIdx(0);
    cancelForm();
  };

  // Manejadores de carga de imágenes
  const handleImageFile = async (file: File, varIdx: number) => {
    setFormError("");
    if (!["image/jpeg", "image/png", "image/webp"].includes(file.type)) {
      setFormError("Usa una imagen JPG, PNG o WebP.");
      return;
    }
    if (file.size > 10 * 1024 * 1024) {
      setFormError("La imagen supera el límite de 10 MB.");
      return;
    }
    try {
      const bitmap = await createImageBitmap(file);
      let scale = Math.min(1, 1400 / Math.max(bitmap.width, bitmap.height));
      const canvas = document.createElement("canvas");
      const context = canvas.getContext("2d");
      if (!context) throw new Error("No se pudo procesar la imagen.");
      let image = "";
      for (let attempt = 0; attempt < 5; attempt++) {
        canvas.width = Math.max(1, Math.round(bitmap.width * scale));
        canvas.height = Math.max(1, Math.round(bitmap.height * scale));
        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(bitmap, 0, 0, canvas.width, canvas.height);
        image = canvas.toDataURL("image/webp", 0.8 - attempt * 0.06);
        if (image.length <= 520000) break;
        scale *= 0.82;
      }
      bitmap.close();
      if (!image || image.length > 520000) throw new Error("La imagen sigue siendo pesada. Reduce su tamaño e inténtalo de nuevo.");
      setForm(current => ({ ...current, variants: current.variants.map((variant, index) => index === varIdx ? { ...variant, image } : variant) }));
    } catch (error) {
      setFormError(error instanceof Error ? error.message : "No se pudo cargar la imagen.");
    }
  };

  // Manejadores de arrastre desde el panel hacia la tienda
  const handleDragStart = (e: React.DragEvent, productId: string) => {
    e.dataTransfer.setData("productId", productId);
    e.dataTransfer.effectAllowed = "copy";
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setIsDragOver(false);
    const productId = e.dataTransfer.getData("productId");
    const prod = products.find(p => p.id === productId);
    if (!prod) return;
    setProducts(prev => prev.map(p => p.id === productId ? { ...p, published: true } : p));
    setActiveCatId(prod.categoryId);
    setActiveProdId(productId);
    setActiveVarIdx(0);
  };

  return (
    <div className="studio-app flex h-screen overflow-hidden text-white" style={{ backgroundColor: bgColor }}>
      {/* ════════════════ SHOWCASE ════════════════ */}
      <div
        className="studio-main flex-1 overflow-y-auto relative min-w-0"
        onDragOver={e => { e.preventDefault(); setIsDragOver(true); }}
        onDragLeave={() => setIsDragOver(false)}
        onDrop={handleDrop}
      >
        <div className="studio-ambient" aria-hidden="true" />

        {/* Drop overlay */}
        <AnimatePresence>
          {isDragOver && (
            <motion.div
              initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }}
              className="absolute inset-0 z-50 flex flex-col items-center justify-center bg-white/5 border-4 border-dashed border-white/40 backdrop-blur-sm rounded-none"
            >
              <span className="text-7xl mb-4">📌</span>
              <p className="text-white text-2xl font-black">Suelta para publicar aquí</p>
              <p className="text-white/60 mt-2 text-sm">El producto aparecerá en la tienda</p>
            </motion.div>
          )}
        </AnimatePresence>

        {/* Nav */}
        <motion.nav
          initial={{ opacity: 0, transform: "translateY(-12px)" }} animate={{ opacity: 1, transform: "translateY(0)" }} transition={{ duration: 0.28, ease: [0.23, 1, 0.32, 1] }}
          className="studio-nav sticky top-0 z-40 bg-black/40 backdrop-blur-xl border-b border-white/10"
        >
          <div className="px-4 sm:px-6 py-3 flex items-center justify-between gap-3">
            <motion.span
              key={activeCatId}
              initial={{ opacity: 0 }} animate={{ opacity: 1 }}
              className={`text-xl font-black bg-gradient-to-r ${activeCat.accentGradient} bg-clip-text text-transparent tracking-tight shrink-0`}
            >
              CENTRAL
            </motion.span>

            {/* Category tabs */}
            <div className="flex items-center gap-1 overflow-x-auto no-scrollbar flex-1 justify-center">
              {activeCatsWithProducts.map(cat => (
                <motion.button
                  key={cat.id}
                  whileHover={{ scale: 1.05 }} whileTap={{ scale: 0.95 }}
                  onClick={() => goCat(cat.id)}
                  aria-pressed={activeCatId === cat.id}
                  aria-label={cat.label}
                  className={`px-3 py-1.5 rounded-full text-xs font-semibold whitespace-nowrap transition-all duration-300 ${
                    activeCatId === cat.id
                      ? `bg-gradient-to-r ${cat.accentGradient} text-white shadow-lg`
                      : "text-white/50 hover:text-white hover:bg-white/10"
                  }`}
                >
                  {cat.emoji} <span className="hidden sm:inline ml-1">{cat.label}</span>
                </motion.button>
              ))}
            </div>

            {/* Panel toggle */}
            <motion.button
              whileHover={{ scale: 1.1 }} whileTap={{ scale: 0.9 }}
              onClick={() => setPanelOpen(o => !o)}
              aria-label={panelOpen ? "Cerrar editor" : "Abrir editor"}
              className="p-2 rounded-xl bg-white/10 hover:bg-white/20 border border-white/20 transition-all shrink-0"
              title={panelOpen ? "Cerrar panel" : "Abrir panel de edición"}
            >
              {panelOpen ? <PanelRightClose size={16} /> : <PanelRightOpen size={16} />}
            </motion.button>
          </div>
        </motion.nav>

        {/* Contenido de la tienda */}
        <div className="studio-content relative z-10 max-w-5xl mx-auto px-4 sm:px-6">
          {catProducts.length === 0 ? (
            /* Empty state */
            <div className="flex flex-col items-center justify-center min-h-[60vh] text-center">
              <div className="text-6xl mb-4">📦</div>
              <h2 className="text-2xl font-bold text-white/60 mb-2">Sin productos publicados</h2>
              <p className="text-white/40 text-sm max-w-xs">
                Arrastra un producto desde el panel de la derecha para publicarlo aquí
              </p>
              <div className="mt-6 flex items-center gap-2 text-white/30 text-sm">
                <span>Panel</span>
                <ChevronRight size={14} />
                <span>Arrastra a esta zona</span>
              </div>
            </div>
          ) : activeProd ? (
            <>
              {/* Hero */}
              <motion.div
                key={`hero-${activeCatId}-${activeProdId}`}
                initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.5 }}
                className="studio-showcase grid lg:grid-cols-2 gap-8 lg:gap-12 items-center py-8 lg:py-12"
              >
                {/* Image */}
                <div className="product-stage">
                  <div className="product-stage-grid" aria-hidden="true" />
                  <div className="product-stage-light" aria-hidden="true" />
                  <motion.div
                    key={`img-${activeProdId}-${activeVarIdx}`}
                    initial={{ opacity: 0, transform: "translateY(12px) scale(.985)" }}
                    animate={{ opacity: 1, transform: "translateY(0) scale(1)" }}
                    transition={{ duration: 0.42, ease: [0.23, 1, 0.32, 1] }}
                    className="product-stage-media"
                  >
                    <img
                      src={activeVar?.image}
                      alt={activeProd.name + " – " + (activeVar?.name ?? "")}
                      className="product-image"
                      loading="eager"
                      decoding="async"
                      draggable={false}
                    />
                  </motion.div>
                  <div className="product-stage-caption">
                    <span>Vista de producto</span>
                    <span>{activeVar?.name ?? "Variante"}</span>
                  </div>
                </div>

                {/* Info */}
                <motion.div
                  key={`info-${activeProdId}`}
                  initial={{ opacity: 0, transform: "translateX(12px)" }} animate={{ opacity: 1, transform: "translateX(0)" }}
                  transition={{ duration: 0.32, delay: 0.04, ease: [0.23, 1, 0.32, 1] }}
                  className="studio-product-info flex flex-col gap-4"
                >
                  <div className="flex items-center gap-3 flex-wrap">
                    <span className="studio-brand text-white/40 text-xs uppercase tracking-widest font-semibold">{activeProd.brand}</span>
                    {activeProd.badge && (
                      <span className={`text-xs font-bold px-3 py-1 rounded-full bg-gradient-to-r ${activeCat.accentGradient} text-white`}>
                        {activeProd.badge}
                      </span>
                    )}
                  </div>

                  <h1 className="studio-product-title text-4xl sm:text-5xl font-black text-white leading-none tracking-tight">
                    {activeProd.name}
                  </h1>

                  <div className="studio-rating flex items-center gap-2">
                    <div className="flex items-center gap-0.5">
                      {[...Array(5)].map((_, i) => (
                        <Star key={i} size={14} className={i < Math.floor(activeProd.rating) ? "fill-amber-400 text-amber-400" : "text-white/20"} />
                      ))}
                    </div>
                    <span className="text-amber-400 font-bold text-sm">{activeProd.rating}</span>
                    <span className="text-white/35 text-sm">({activeProd.reviews.toLocaleString()} reseñas)</span>
                  </div>

                  <p className="studio-description text-white/65 text-sm lg:text-base leading-relaxed">{activeProd.description}</p>

                  <div className="studio-features grid grid-cols-2 gap-1.5">
                    {activeProd.features.map(f => (
                      <div key={f} className="flex items-center gap-2 text-white/55 text-xs">
                        <Check size={11} className="text-emerald-400 shrink-0" /> {f}
                      </div>
                    ))}
                  </div>

                  {/* Variants */}
                  <div className="studio-variants">
                    <p className="text-white/40 text-xs uppercase tracking-wider mb-2">
                      Color: <span className="text-white font-semibold normal-case tracking-normal">{activeVar?.name}</span>
                    </p>
                    <div className="flex items-center gap-2">
                      {activeProd.variants.map((v, idx) => (
                        <motion.button
                          key={`${v.id}-${idx}`}
                          whileHover={{ scale: 1.2 }} whileTap={{ scale: 0.9 }}
                          onClick={() => setActiveVarIdx(idx)}
                          aria-label={`Seleccionar color ${v.name}`}
                          aria-pressed={activeVarIdx === idx}
                          className={`w-9 h-9 rounded-full bg-gradient-to-br ${v.gradient} transition-all duration-300 ${
                            activeVarIdx === idx
                              ? "ring-2 ring-white ring-offset-2 ring-offset-black"
                              : "ring-1 ring-white/20 hover:ring-white/40"
                          }`}
                          title={v.name}
                        />
                      ))}
                    </div>
                  </div>

                  {/* Price */}
                  <div className="studio-price flex items-baseline gap-3 flex-wrap">
                    <span className="text-4xl font-black text-white">${activeProd.basePrice.toFixed(2)}</span>
                    {activeProd.originalPrice > activeProd.basePrice && (
                      <span className="text-white/35 line-through text-lg">${activeProd.originalPrice.toFixed(2)}</span>
                    )}
                    {discount > 0 && (
                      <span className={`text-xs font-black px-2 py-1 rounded-lg bg-gradient-to-r ${activeCat.accentGradient} text-white`}>
                        -{discount}%
                      </span>
                    )}
                  </div>

                  {/* Buy buttons */}
                  <div className="studio-buy-actions flex flex-wrap gap-2 pt-1">
                    {activeProd.amazon && (
                      <motion.a
                        href={activeProd.amazon} target="_blank" rel="noopener noreferrer"
                        whileHover={{ scale: 1.03, y: -2 }} whileTap={{ scale: 0.97 }}
                        className="studio-buy-link studio-buy-link--amazon flex items-center gap-2 bg-gradient-to-r from-amber-400 to-orange-500 text-black font-black px-5 py-3 rounded-2xl hover:shadow-xl hover:shadow-orange-500/30 transition-all text-sm"
                      >
                        Ver en <strong>Amazon</strong> <ExternalLink size={13} />
                      </motion.a>
                    )}
                    {activeProd.aliexpress && (
                      <motion.a
                        href={activeProd.aliexpress} target="_blank" rel="noopener noreferrer"
                        whileHover={{ scale: 1.03, y: -2 }} whileTap={{ scale: 0.97 }}
                        className="studio-buy-link studio-buy-link--aliexpress flex items-center gap-2 bg-gradient-to-r from-yellow-300 to-yellow-500 text-black font-black px-5 py-3 rounded-2xl hover:shadow-xl hover:shadow-yellow-400/30 transition-all text-sm"
                      >
                        Ver en <strong>AliExpress</strong> <ExternalLink size={13} />
                      </motion.a>
                    )}
                    {activeProd.wompi && (
                      <motion.a
                        href={activeProd.wompi} target="_blank" rel="noopener noreferrer"
                        whileHover={{ scale: 1.03, y: -2 }} whileTap={{ scale: 0.97 }}
                        className="studio-buy-link studio-buy-link--wompi flex items-center gap-2 bg-gradient-to-r from-blue-500 to-indigo-600 text-white font-black px-5 py-3 rounded-2xl hover:shadow-xl hover:shadow-blue-500/30 transition-all text-sm"
                      >
                        Pagar con <strong>Wompi</strong> <ExternalLink size={13} />
                      </motion.a>
                    )}
                  </div>
                </motion.div>
              </motion.div>

              {/* Product grid */}
              {catProducts.length > 1 && (
                <div className="studio-related pb-16">
                  <div className="flex items-center gap-2 mb-4">
                    <div className={`w-1 h-5 rounded-full bg-gradient-to-b ${activeCat.accentGradient}`} />
                    <h2 className="text-base font-bold text-white/60">
                      Más en <span className={`bg-gradient-to-r ${activeCat.accentGradient} bg-clip-text text-transparent`}>{activeCat.label}</span>
                    </h2>
                  </div>
                  <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 gap-3">
                    {catProducts.map(p => (
                      <motion.button
                        key={p.id}
                        whileHover={{ scale: 1.03, y: -4 }} whileTap={{ scale: 0.97 }}
                        onClick={() => goProd(p.id)}
                        className={`studio-product-card relative text-left rounded-2xl overflow-hidden border transition-all duration-300 ${
                          activeProdId === p.id ? "border-white/40 bg-white/10" : "border-white/10 bg-white/5 hover:border-white/25"
                        }`}
                      >
                        {activeProdId === p.id && (
                          <div className={`absolute inset-0 bg-gradient-to-br ${activeCat.accentGradient} opacity-10 pointer-events-none`} />
                        )}
                        <img src={p.variants[0]?.image} alt={p.name} className="studio-product-card-image w-full h-32 object-cover" />
                        <div className="p-2.5">
                          <p className="text-white/40 text-xs font-semibold uppercase">{p.brand}</p>
                          <p className="text-white font-bold text-sm truncate">{p.name}</p>
                          <div className="flex items-center justify-between mt-1">
                            <span className="text-white font-black text-sm">${p.basePrice}</span>
                            {p.badge && (
                              <span className={`text-xs px-1.5 py-0.5 rounded-full bg-gradient-to-r ${activeCat.accentGradient} text-white font-semibold`}>
                                {p.badge}
                              </span>
                            )}
                          </div>
                        </div>
                      </motion.button>
                    ))}
                  </div>
                </div>
              )}
            </>
          ) : null}
        </div>
      </div>

      {/* ════════════════ EDITOR PANEL ════════════════ */}
      <AnimatePresence>
        {panelOpen && (
          <motion.aside
            initial={{ x: "100%", opacity: 0 }}
            animate={{ x: 0, opacity: 1 }}
            exit={{ x: "100%", opacity: 0 }}
            transition={{ type: "spring", damping: 28, stiffness: 220 }}
            className="studio-panel w-[380px] shrink-0 bg-gray-950 border-l border-white/10 flex flex-col h-screen overflow-hidden"
          >
            {/* Panel header */}
            <div className="studio-panel-header px-4 py-3 border-b border-white/10 flex items-center justify-between">
              <div><h2 className="text-sm font-black text-white/80 uppercase tracking-widest">Studio de producto</h2><p className="studio-local-note">Los cambios se guardan en este navegador</p></div>
              <span className={`studio-storage-status ${storageStatus === "saved" ? "is-saved" : "is-error"}`} role="status" aria-live="polite">{storageStatus === "saved" ? "Guardado" : "Error al guardar"}</span>
              <button
                onClick={() => setPanelOpen(false)}
                aria-label="Cerrar editor"
                className="p-1.5 rounded-lg hover:bg-white/10 text-white/50 hover:text-white transition-all"
              >
                <X size={15} />
              </button>
            </div>

            {/* Tabs */}
            <div className="studio-panel-tabs flex border-b border-white/10">
              {(["productos", "agregar", "apariencia"] as const).map(tab => (
                <button
                  key={tab}
                  onClick={() => { if (tab !== "agregar") { setEditingId(null); setForm(newEmptyForm()); setFormError(""); } setPanelTab(tab); }}
                  aria-pressed={panelTab === tab}
                  aria-label={tab === "productos" ? "Productos" : tab === "agregar" ? "Agregar producto" : "Apariencia"}
                  className={`flex-1 py-2.5 text-xs font-bold uppercase tracking-wider transition-all ${
                    panelTab === tab
                      ? "text-white border-b-2 border-white"
                      : "text-white/40 hover:text-white/70"
                  }`}
                >
                  {tab === "productos" ? "📦 Productos" : tab === "agregar" ? "✏️ Agregar" : "🎨 Tema"}
                </button>
              ))}
            </div>

            {/* Panel content */}
            <div className="studio-panel-content flex-1 overflow-y-auto">

              {/* ── TAB: PRODUCTOS ── */}
              {panelTab === "productos" && (
                <div className="p-3 flex flex-col gap-2">
                  <div className="flex items-center justify-between mb-1">
                    <p className="text-white/40 text-xs uppercase tracking-wider">{products.length} productos</p>
                    <button
                      onClick={() => { setForm(newEmptyForm()); setEditingId(null); setPanelTab("agregar"); }}
                      className="flex items-center gap-1 text-xs font-bold text-white/60 hover:text-white bg-white/10 hover:bg-white/20 px-2.5 py-1.5 rounded-lg transition-all"
                    >
                      <Plus size={12} /> Nuevo
                    </button>
                  </div>

                  <p className="studio-editor-hint text-white/30 text-xs text-center py-1 flex items-center justify-center gap-1">
                    <GripVertical size={11} /> Arrastra un producto o usa el control de publicación
                  </p>

                  {CATEGORIES.filter(c => products.some(p => p.categoryId === c.id)).map(cat => (
                    <div key={cat.id} className="mb-1">
                      <p className="text-white/30 text-xs uppercase tracking-wider px-1 mb-1.5">
                        {cat.emoji} {cat.label}
                      </p>
                      {products.filter(p => p.categoryId === cat.id).map(prod => (
                        <div
                          key={prod.id}
                          draggable
                          onDragStart={e => handleDragStart(e, prod.id)}
                          className="flex items-center gap-2 bg-white/5 hover:bg-white/10 border border-white/10 hover:border-white/20 rounded-xl p-2 mb-1.5 cursor-grab active:cursor-grabbing transition-all group"
                        >
                          <GripVertical size={14} className="text-white/20 group-hover:text-white/50 shrink-0" />
                          <img
                            src={prod.variants[0]?.image}
                            alt={prod.name}
                            className="w-10 h-10 object-cover rounded-lg shrink-0"
                          />
                          <div className="flex-1 min-w-0">
                            <p className="text-white text-xs font-bold truncate">{prod.name}</p>
                            <p className="text-white/40 text-xs">${prod.basePrice}</p>
                          </div>
                          <div className="flex items-center gap-1 shrink-0">
                            <button
                              onClick={() => togglePublished(prod.id)}
                              className={`p-1.5 rounded-lg transition-all ${prod.published ? "text-emerald-400 hover:bg-emerald-400/10" : "text-white/30 hover:bg-white/10"}`}
                              title={prod.published ? "Publicado – clic para ocultar" : "Oculto – clic para publicar"}
                            >
                              {prod.published ? <Eye size={13} /> : <EyeOff size={13} />}
                            </button>
                            <button
                              onClick={() => startEditing(prod)}
                              aria-label={`Editar ${prod.name}`}
                              className="p-1.5 rounded-lg text-white/30 hover:text-white hover:bg-white/10 transition-all"
                            >
                              <Edit3 size={13} />
                            </button>
                            <button
                              onClick={() => deleteProduct(prod.id)}
                              aria-label={`Eliminar ${prod.name}`}
                              className="p-1.5 rounded-lg text-white/30 hover:text-red-400 hover:bg-red-400/10 transition-all"
                            >
                              <Trash2 size={13} />
                            </button>
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
                </div>
              )}

              {/* ── TAB: AGREGAR / EDITAR ── */}
              {panelTab === "agregar" && (
                <div className="p-4 flex flex-col gap-4">
                  <p className="text-white/50 text-xs uppercase tracking-wider">
                    {editingId ? "Editando producto" : "Nuevo producto"}
                  </p>

                  {/* Nombre + Marca */}
                  <div className="flex flex-col gap-2">
                    <label className="text-white/60 text-xs font-semibold">Nombre del producto *</label>
                    <input
                      value={form.name}
                      onChange={e => setForm(f => ({ ...f, name: e.target.value }))}
                      placeholder="Nombre del producto"
                      className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                    />
                  </div>
                  <div className="flex gap-2">
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Marca</label>
                      <input
                        value={form.brand}
                        onChange={e => setForm(f => ({ ...f, brand: e.target.value }))}
                        placeholder="Marca"
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Categoría</label>
                      <select
                        value={form.categoryId}
                        onChange={e => setForm(f => ({ ...f, categoryId: e.target.value }))}
                        className="w-full bg-gray-900 border border-white/15 rounded-xl px-3 py-2 text-sm text-white focus:border-white/40 outline-none transition-all"
                      >
                        {CATEGORIES.map(c => (
                          <option key={c.id} value={c.id}>{c.emoji} {c.label}</option>
                        ))}
                      </select>
                    </div>
                  </div>

                  {/* Descripción */}
                  <div className="flex flex-col gap-2">
                    <label className="text-white/60 text-xs font-semibold">Descripción</label>
                    <textarea
                      value={form.description}
                      onChange={e => setForm(f => ({ ...f, description: e.target.value }))}
                      placeholder="Descripción del producto"
                      rows={2}
                      className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none resize-none transition-all"
                    />
                  </div>

                  {/* Badge */}
                  <div className="flex flex-col gap-2">
                    <label className="text-white/60 text-xs font-semibold">Etiqueta (opcional)</label>
                    <input
                      value={form.badge}
                      onChange={e => setForm(f => ({ ...f, badge: e.target.value }))}
                      placeholder="Etiqueta"
                      className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                    />
                  </div>

                  {/* Precios */}
                  <div className="flex gap-2">
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Precio ($) *</label>
                      <input
                        type="number" value={form.basePrice}
                        onChange={e => setForm(f => ({ ...f, basePrice: e.target.value }))}
                        placeholder="Precio de venta"
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Precio original ($)</label>
                      <input
                        type="number" value={form.originalPrice}
                        onChange={e => setForm(f => ({ ...f, originalPrice: e.target.value }))}
                        placeholder="Precio original"
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                  </div>
                  <div className="flex gap-2">
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Calificación (0–5)</label>
                      <input
                        type="number" min="0" max="5" step="0.1" value={form.rating}
                        onChange={e => setForm(f => ({ ...f, rating: e.target.value }))}
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                    <div className="flex flex-col gap-2 flex-1">
                      <label className="text-white/60 text-xs font-semibold">Número de reseñas</label>
                      <input
                        type="number" value={form.reviews}
                        onChange={e => setForm(f => ({ ...f, reviews: e.target.value }))}
                        placeholder="0"
                        className="w-full bg-white/5 border border-white/15 rounded-xl px-3 py-2 text-sm text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                  </div>

                  {/* Características */}
                  <div className="flex flex-col gap-2">
                    <label className="text-white/60 text-xs font-semibold">Características (hasta 4)</label>
                    <div className="grid grid-cols-2 gap-1.5">
                      {form.features.map((f, i) => (
                        <input
                          key={i} value={f}
                          onChange={e => setForm(fm => ({ ...fm, features: fm.features.map((x, j) => j === i ? e.target.value : x) }))}
                          placeholder={`Característica ${i + 1}`}
                          className="bg-white/5 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                        />
                      ))}
                    </div>
                  </div>

                  {/* Variantes de color */}
                  <div className="flex flex-col gap-2">
                    <div className="flex items-center justify-between">
                      <label className="text-white/60 text-xs font-semibold">Variantes de color *</label>
                      {form.variants.length < 4 && (
                        <button
                          onClick={() => setForm(f => ({ ...f, variants: [...f.variants, { id: mkId(), name: "", image: "", gradient: GRAD_PRESETS[0].value, glow: GRAD_PRESETS[0].glow }] }))}
                          className="text-xs text-white/50 hover:text-white flex items-center gap-1 transition-all"
                        >
                          <Plus size={11} /> Agregar
                        </button>
                      )}
                    </div>

                    {form.variants.map((v, vi) => (
                      <div key={v.id} className="bg-white/5 border border-white/10 rounded-xl p-3 flex flex-col gap-2">
                        <div className="flex items-center justify-between">
                          <span className="text-white/50 text-xs font-semibold">Variante {vi + 1}</span>
                          {form.variants.length > 1 && (
                            <button
                              onClick={() => setForm(f => ({ ...f, variants: f.variants.filter((_, j) => j !== vi) }))}
                              className="text-red-400/60 hover:text-red-400 transition-all"
                            >
                              <X size={12} />
                            </button>
                          )}
                        </div>
                        <input
                          value={v.name}
                          onChange={e => setForm(f => ({ ...f, variants: f.variants.map((x, j) => j === vi ? { ...x, name: e.target.value } : x) }))}
                          placeholder="Nombre del color"
                          className="w-full bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/30 outline-none"
                        />
                        {/* Image: upload or URL */}
                        <div className="flex gap-2">
                          <input
                            value={v.image.startsWith("data:") ? "" : v.image}
                            onChange={e => setForm(f => ({ ...f, variants: f.variants.map((x, j) => j === vi ? { ...x, image: e.target.value } : x) }))}
                            placeholder="URL de imagen"
                            className="flex-1 bg-white/5 border border-white/10 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/30 outline-none"
                          />
                          <label className="flex items-center gap-1 px-2.5 py-1.5 bg-white/10 hover:bg-white/20 rounded-lg cursor-pointer transition-all text-white/60 hover:text-white text-xs" title="Subir imagen">
                            <Upload size={12} />
                            <input
                              type="file" accept="image/jpeg,image/png,image/webp" className="hidden"
                              onChange={e => { const f = e.target.files?.[0]; if (f) handleImageFile(f, vi); }}
                            />
                          </label>
                        </div>
                        {/* Preview */}
                        {v.image && (
                          <img src={v.image} alt="Vista previa" className="w-full h-20 object-contain rounded-lg opacity-90 bg-black/20" />
                        )}
                        {/* Gradient picker */}
                        <div>
                          <p className="text-white/30 text-xs mb-1.5">Gradiente del color</p>
                          <div className="flex flex-wrap gap-1.5">
                            {GRAD_PRESETS.map(g => (
                              <button
                                key={g.id}
                                onClick={() => setForm(f => ({ ...f, variants: f.variants.map((x, j) => j === vi ? { ...x, gradient: g.value, glow: g.glow } : x) }))}
                                className={`w-6 h-6 rounded-full bg-gradient-to-br ${g.value} transition-all ${v.gradient === g.value ? "ring-2 ring-white ring-offset-1 ring-offset-gray-950" : "opacity-60 hover:opacity-100"}`}
                                title={g.label}
                              />
                            ))}
                          </div>
                        </div>
                      </div>
                    ))}
                  </div>

                  {/* Links de compra */}
                  <div className="flex flex-col gap-2">
                    <label className="text-white/60 text-xs font-semibold flex items-center gap-1.5">
                      <Link2 size={12} /> Links de compra
                    </label>
                    <div className="flex items-center gap-2">
                      <span className="text-amber-400 text-xs w-20 shrink-0 font-bold">Amazon</span>
                      <input
                        value={form.amazon}
                        onChange={e => setForm(f => ({ ...f, amazon: e.target.value }))}
                        placeholder="Enlace del producto en Amazon"
                        className="flex-1 bg-white/5 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-yellow-400 text-xs w-20 shrink-0 font-bold">AliExpress</span>
                      <input
                        value={form.aliexpress}
                        onChange={e => setForm(f => ({ ...f, aliexpress: e.target.value }))}
                        placeholder="Enlace del producto en AliExpress"
                        className="flex-1 bg-white/5 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="text-blue-400 text-xs w-20 shrink-0 font-bold">Wompi</span>
                      <input
                        value={form.wompi}
                        onChange={e => setForm(f => ({ ...f, wompi: e.target.value }))}
                        placeholder="Enlace de pago de Wompi"
                        className="flex-1 bg-white/5 border border-white/15 rounded-lg px-2.5 py-1.5 text-xs text-white placeholder-white/30 focus:border-white/40 outline-none transition-all"
                      />
                    </div>
                  </div>

                  {formError && <p className="studio-form-error" role="alert">{formError}</p>}
                  {/* Actions */}
                  <div className="flex gap-2 pt-1 pb-4">
                    <button
                      onClick={submitForm}
                      disabled={!form.name.trim() || !form.variants.some(variant => variant.name.trim() && variant.image)}
                      className="flex-1 bg-white text-black font-black text-sm py-3 rounded-2xl hover:bg-white/90 disabled:opacity-30 disabled:cursor-not-allowed transition-all"
                    >
                      {editingId ? "Guardar cambios" : "Publicar producto"}
                    </button>
                    <button
                      onClick={cancelForm}
                      className="px-4 py-3 rounded-2xl border border-white/20 text-white/60 hover:text-white hover:border-white/40 transition-all text-sm"
                    >
                      Cancelar
                    </button>
                  </div>
                </div>
              )}

              {/* ── TAB: APARIENCIA ── */}
              {panelTab === "apariencia" && (
                <div className="p-4 flex flex-col gap-4">
                  <p className="text-white/50 text-xs uppercase tracking-wider flex items-center gap-1.5">
                    <Palette size={12} /> Color de fondo
                  </p>
                  <div className="grid grid-cols-2 gap-2">
                    {BG_THEMES.map(t => (
                      <button
                        key={t.id}
                        onClick={() => setBgTheme(t.id)}
                        className={`flex items-center gap-2 px-3 py-2 rounded-xl border text-xs font-semibold transition-all ${
                          bgTheme === t.id
                            ? "border-white/60 bg-white/10 text-white"
                            : "border-white/10 text-white/50 hover:border-white/30 hover:text-white"
                        }`}
                      >
                        <span
                          className="w-5 h-5 rounded-full border border-white/30"
                          style={{ backgroundColor: t.color }}
                        />
                        {t.label}
                      </button>
                    ))}
                  </div>
                </div>
              )}

            </div>
          </motion.aside>
        )}
      </AnimatePresence>
    </div>
  );
}