'use strict'

const fs = require('fs')
const path = require('path')
const mineflayer = require('mineflayer')
const { pathfinder, Movements, goals } = require('mineflayer-pathfinder')

const configPath = path.join(__dirname, 'config.json')
if (!fs.existsSync(configPath)) {
  console.error('Falta config.json. Copiá config.example.json y completá tus datos.')
  process.exit(2)
}

const config = JSON.parse(fs.readFileSync(configPath, 'utf8'))
if (!config.owner || config.owner === 'TuUsuarioDeMinecraft') {
  console.error('Definí tu nombre exacto de Minecraft en el campo owner de config.json.')
  process.exit(2)
}

const bot = mineflayer.createBot({
  host: config.host,
  port: Number(config.port || 25565),
  username: config.username || 'Rebecca',
  auth: config.auth || 'offline'
})

bot.loadPlugin(pathfinder)
let movements

function ownerEntity () {
  return bot.players[config.owner]?.entity
}

function followOwner () {
  const owner = ownerEntity()
  if (!owner) return bot.chat('No te veo todavía.')
  bot.pathfinder.setMovements(movements)
  bot.pathfinder.setGoal(new goals.GoalFollow(owner, 2), true)
  bot.chat('Voy con vos.')
}

async function askRebecca (message) {
  const owner = ownerEntity()
  const context = owner
    ? `Minecraft: estoy cerca de ${config.owner} en ${JSON.stringify(owner.position)}. Vida propia: ${bot.health}.`
    : `Minecraft: no veo a ${config.owner}. Vida propia: ${bot.health}.`
  const response = await fetch(config.rebeccaWebhook, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify({ text: `${context} ${message}`, source: 'minecraft' })
  })
  if (!response.ok) throw new Error(`n8n respondió ${response.status}`)
  let data = await response.json()
  if (Array.isArray(data)) data = data[0]
  return String(data.output || data.message || data.text || '').trim()
}

bot.once('spawn', () => {
  movements = new Movements(bot)
  movements.canDig = false
  bot.pathfinder.setMovements(movements)
  console.log('Rebecca entró al mundo. Solo acepta órdenes del owner configurado.')
  if (config.autoFollow) followOwner()
})

bot.on('chat', async (username, message) => {
  if (username !== config.owner) return
  const command = message.trim().toLowerCase()
  try {
    if (['!seguime', '!ven', '!follow'].includes(command)) return followOwner()
    if (['!quieta', '!stop'].includes(command)) {
      bot.pathfinder.setGoal(null)
      bot.clearControlStates()
      return bot.chat('Me quedo acá.')
    }
    if (command === '!salta') {
      bot.setControlState('jump', true)
      setTimeout(() => bot.setControlState('jump', false), 350)
      return
    }
    if (command === '!estado') {
      return bot.chat(`Tengo ${Math.round(bot.health)} de vida y ${Math.round(bot.food)} de comida.`)
    }
    if (command.startsWith('rebecca ')) {
      const answer = await askRebecca(message.slice(8))
      if (answer) bot.chat(answer.slice(0, 240))
    }
  } catch (error) {
    console.error(error)
    bot.chat('Se cortó mi conexión con Rebecca, bancame un toque.')
  }
})

bot.on('kicked', reason => console.error('Rebecca fue expulsada:', reason))
bot.on('error', error => console.error('Error de Minecraft:', error.message))
bot.on('end', () => console.log('Rebecca salió del servidor.'))

process.on('SIGINT', () => {
  bot.clearControlStates()
  bot.quit('Cerrando Rebecca de forma segura')
  setTimeout(() => process.exit(0), 500)
})
