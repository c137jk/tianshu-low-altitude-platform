/**
 * 本地日期格式化。
 *
 * 为什么不用 toISOString().slice(0,10)：它走 UTC，东八区凌晨 0~8 点会退成昨天。
 * 表现是"今天没人排班""维保到期提醒差一天""导出文件名日期比数据早一天"，
 * 排查时看着像数据坏了，其实只是取日期的写法错了。
 */
const p = (n: number) => String(n).padStart(2, '0')

/** 'YYYY-MM-DD' */
export function ymd(d: Date): string {
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
}

/** 今天：'YYYY-MM-DD' */
export function today(): string {
  return ymd(new Date())
}

/** 'YYYY-MM-DD HH:mm:ss' */
export function ymdHms(d: Date): string {
  return `${ymd(d)} ${p(d.getHours())}:${p(d.getMinutes())}:${p(d.getSeconds())}`
}

/** 加减天数，输入输出都是 'YYYY-MM-DD'。按日历日算，用本地零点避免夏令时/时区漂移。 */
export function plusDays(date: string, n: number): string {
  const d = new Date(`${date}T00:00:00`)
  d.setDate(d.getDate() + n)
  return ymd(d)
}
