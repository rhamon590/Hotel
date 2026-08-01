function toggleTheme(){const h=document.documentElement;h.dataset.theme=h.dataset.theme==='dark'?'light':'dark';localStorage.setItem('theme',h.dataset.theme)}
document.documentElement.dataset.theme=localStorage.getItem('theme')||'dark';
if(document.getElementById('occChart')&&window.chartData){new Chart(document.getElementById('occChart'),{type:'doughnut',data:{labels:['Ocupados','Livres','Reservados','Limpeza','Manutenção'],datasets:[{data:window.chartData}]},options:{plugins:{legend:{position:'bottom'}}}})}
